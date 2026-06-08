"""메타 자동 감시 — 파이프라인 DOWN / 중앙 디스크 임계 시 경보.

상태 '변화'에만 알림(중복 방지): 문제가 새로 생기면 경보, 해소되면 복구 알림.
대상은 settings.meta_alert_email(없으면 alert_email) + meta_slack_webhook(선택).
※ 한계: 중앙 app 자체가 죽으면 이 루프도 멈춤 → 그건 외부 업타임 체크 영역.
   여기선 'app은 살아있는데 의존 컴포넌트(vmalert/AM/VM/VL)가 죽거나 디스크가 차는' 조용한 실패를 잡는다.
※ 에이전트 무응답은 기존 dead-man(deadman.py)이 이미 처리하므로 중복하지 않음.
"""
import asyncio
import logging

from src.config import settings
from src.health import gather_system_status

logger = logging.getLogger(__name__)

# 모듈 수준 상태 — 현재 발화 중인 문제 키 집합
_state = {"problems": set(), "started": False}


def _current_problems(st: dict) -> dict:
    """{key: 사람이 읽는 설명} 형태로 현재 문제들을 수집."""
    probs = {}
    for c in st.get("checks", []):
        if not c.get("ok"):
            probs[f"pipe:{c['name']}"] = f"{c['name']} DOWN ({c.get('info','')})"
    d = st.get("disk")
    if d and d.get("pct") is not None and d["pct"] >= settings.disk_alert_pct:
        probs["disk"] = f"중앙 디스크 {d['pct']}% (임계 {settings.disk_alert_pct}%, 여유 {d.get('free_gb')}GB)"
    return probs


def _alert(subject: str, lines: list[str]) -> None:
    body = "\n".join(f"• {l}" for l in lines)
    logger.warning("meta-alert: %s | %s", subject, " / ".join(lines))
    target = settings.meta_alert_email or settings.alert_email
    if target:
        html = (
            f"<div style='font-family:sans-serif'>"
            f"<h3 style='margin:0 0 10px'>{subject}</h3>"
            f"<ul>{''.join(f'<li>{l}</li>' for l in lines)}</ul>"
            f"<p style='color:#718096;font-size:12px'>AI 장애 대응 — 시스템 자가 감시(메타 모니터링)</p></div>"
        )
        try:
            from src.notifier.email_notifier import send_plain
            send_plain(target, f"[AI장애대응|시스템] {subject}", html)
        except Exception as e:
            logger.warning("meta-alert 메일 실패: %s", e)
    if settings.meta_slack_webhook:
        try:
            from src.notifier.slack_notifier import send_text
            send_text(settings.meta_slack_webhook, f"*{subject}*\n{body}")
        except Exception as e:
            logger.warning("meta-alert Slack 실패: %s", e)


def check_system_once() -> None:
    st = gather_system_status()
    probs = _current_problems(st)
    keys = set(probs.keys())
    old = _state["problems"]

    new_firing = keys - old
    recovered = old - keys

    if new_firing:
        _alert("⚠ 시스템 이상 감지", [probs[k] for k in sorted(new_firing)])
    if recovered and _state["started"]:
        _alert("✅ 시스템 복구", [f"복구됨: {k}" for k in sorted(recovered)])

    _state["problems"] = keys
    _state["started"] = True


async def system_monitor_loop() -> None:
    if not settings.sysmonitor_enabled:
        return
    await asyncio.sleep(60)  # 기동 직후 컴포넌트 워밍업 grace
    while True:
        try:
            await asyncio.to_thread(check_system_once)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("sysmonitor 점검 실패: %s", e)
        try:
            await asyncio.sleep(settings.sysmonitor_interval_seconds)
        except asyncio.CancelledError:
            break
