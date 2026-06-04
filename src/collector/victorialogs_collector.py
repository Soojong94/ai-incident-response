"""
VictoriaLogs 로그 수집기 (에이전트 기반).

알람 발생 host의 직전 N분 로그를 LogsQL로 쿼리해 줄 단위로 반환한다.
`obs_collector.collect` 와 **동일한 list[str] 형태**를 반환하므로 analyzer/파이프라인은 무변경 재사용.

- 운영: 같은 사설 subnet의 monitoring_msp VictoriaLogs를 사설 IP로 쿼리 (무인증 → 공인 노출 금지).
- 쿼리: `{host="<server_name>"} _time:[start, end]` → NDJSON 응답을 `@timestamp/type/message` 형태로 매핑.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_end(end) -> datetime:
    """알람 시각(end) 정규화 — datetime / ISO 문자열 / None 허용."""
    if isinstance(end, datetime):
        return end
    if isinstance(end, str) and end.strip():
        try:
            return datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


async def collect(host: str, end=None, window_seconds: int | None = None) -> list[str]:
    """host 의 [end-window, end] 구간 로그를 VictoriaLogs에서 조회.

    반환: "<ts> [<type>] <message>" 문자열 리스트 (시간순, 최근 100줄).
    실패는 호출측(run_pipeline)에서 graceful degradation 처리.
    """
    window = window_seconds or settings.log_window_seconds
    end_dt = _parse_end(end)
    start_dt = end_dt - timedelta(seconds=window)
    # host는 필드 필터(host:=)로 매칭 — Alloy의 Loki push는 host를 스트림 필드가 아닌
    # 일반 필드로 저장하므로 `{host="..."}` 스트림 필터로는 안 잡힌다. (필드 필터는 둘 다 매칭)
    query = f'host:={json.dumps(host)} _time:[{_rfc3339(start_dt)}, {_rfc3339(end_dt)}]'
    url = settings.victorialogs_url.rstrip("/") + "/select/logsql/query"

    logger.info("VictoriaLogs 수집 시작: host=%s, window=%ds", host, window)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, data={"query": query})
        resp.raise_for_status()
        body = resp.text

    records: list[tuple[str, str]] = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            records.append(("", line))
            continue
        ts = rec.get("_time", "")
        log_type = rec.get("job") or rec.get("filename") or "log"
        message = rec.get("_msg", "")
        records.append((ts, f"{ts} [{log_type}] {message}"))

    records.sort(key=lambda x: x[0])
    logs = [entry for _, entry in records[-100:]]
    logger.info("VictoriaLogs 로그 %d줄 수집 완료 (전체 %d줄)", len(logs), len(records))
    return logs
