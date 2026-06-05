"""서버 무응답(dead-man) 감지.

활성이던 서버가 N초(기본 180=3분) 이상 메트릭을 안 보내면 "서버 무응답" 장애를 자동 등록하고
해당 사이트 수신자에게 알림한다. 서버가 꺼지거나 에이전트가 죽어도 알람이 가도록 하는 장치.

VictoriaMetrics에 `(time() - timestamp(last_over_time(node_uname_info[30m])))` 를 물어
host별 '마지막 수신 후 경과초'를 구하고, 임계 초과 + 사이트 alarm_enabled면 발화한다.
중복 방지: 모듈 내 _down 집합으로 상태 전이(정상→다운)에서만 1회 발화, 복구 시 해제.
"""
import asyncio
import logging
from datetime import datetime, timezone

import httpx

from src.config import settings
from src.db.database import SessionLocal
from src.db.crud import create_incident, create_analysis, update_incident_status, _match_or_create_site

logger = logging.getLogger(__name__)

_down: set[str] = set()

_DEADMAN_ANALYSIS = {
    "cause_category": "인프라 이슈",
    "cause_detail": "3분 이상 메트릭이 수신되지 않습니다. 서버 다운, 네트워크 단절, 또는 에이전트(alloy-air) 중단이 의심됩니다.",
    "severity": "Critical",
    "impact_scope": "해당 서버 전체 — 서비스 중단 가능성",
    "immediate_actions": [
        "서버 전원/콘솔 및 네트워크 상태 확인",
        "에이전트 동작 확인: systemctl status alloy-air",
        "원격 접속(SSH) 시도 및 호스트 헬스체크",
    ],
    "prevention": "에이전트 systemd 자동복구(Restart=always) 확인, 호스트 헬스체크/오토힐링 점검",
    "confidence": "높음",
}


def _last_seen_seconds() -> dict[str, float]:
    """host별 '마지막 메트릭 수신 후 경과초'. 최근 30분 내 데이터가 있던 host만."""
    url = settings.victoriametrics_url.rstrip("/") + "/api/v1/query"
    query = "(time() - timestamp(last_over_time(node_uname_info[30m])))"
    out: dict[str, float] = {}
    with httpx.Client(timeout=10) as c:
        r = c.get(url, params={"query": query})
        r.raise_for_status()
        data = r.json()
    if data.get("status") == "success":
        for s in data.get("data", {}).get("result", []):
            host = s.get("metric", {}).get("host")
            if not host:
                continue
            try:
                out[host] = float(s.get("value", [None, "nan"])[1])
            except (TypeError, ValueError, IndexError):
                continue
    return out


def check_deadman_once() -> None:
    """1회 점검 — 동기. 백그라운드 루프에서 to_thread로 호출."""
    threshold = settings.deadman_seconds
    seen = _last_seen_seconds()
    db = SessionLocal()
    try:
        for host, secs in seen.items():
            if secs <= threshold:
                _down.discard(host)   # 정상/복구
                continue
            if host in _down:
                continue              # 이미 발화함
            site = _match_or_create_site(db, host)
            if not site or not site.enabled or not getattr(site, "alarm_enabled", True):
                _down.add(host)       # 감시 대상 아님 — 반복 평가만 막음
                continue
            _raise_deadman(db, host, site, secs)
            _down.add(host)
    finally:
        db.close()


def _raise_deadman(db, host: str, site, secs: float) -> None:
    from src.webhook.handler import _dispatch_notifications
    alarm = {
        "alarmName": "서버 무응답",
        "resourceName": host,
        "metricType": "deadman",
        "currentValue": f"{int(secs)}s 미수신",
        "alarmTime": datetime.now(timezone.utc).isoformat(),
        "vl_host": host,
        "source": "deadman",
    }
    inc = create_incident(db, alarm)
    create_analysis(db, inc.id, _DEADMAN_ANALYSIS)
    update_incident_status(db, inc.id, "analyzed", severity="Critical")
    _dispatch_notifications(db, inc.id, "서버 무응답", _DEADMAN_ANALYSIS, "Critical", site_id=site.id)
    logger.warning("서버 무응답 감지: host=%s (%ds 미수신) → incident=%d", host, int(secs), inc.id)


async def deadman_loop() -> None:
    """기동 후 주기적으로 무응답 점검. lifespan에서 create_task."""
    if not settings.deadman_enabled:
        return
    await asyncio.sleep(20)  # 기동 직후 메트릭 안정화 대기
    while True:
        try:
            await asyncio.to_thread(check_deadman_once)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("dead-man 점검 실패: %s", e)
        try:
            await asyncio.sleep(settings.deadman_check_interval)
        except asyncio.CancelledError:
            break
