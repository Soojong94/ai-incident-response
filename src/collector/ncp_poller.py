"""
NCP Cloud Insight 이벤트 폴러.
1분 주기로 SearchEvent를 호출해 새 알람을 감지하고 처리 파이프라인을 시작.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

CI_BASE = "https://cw.apigw.ntruss.com"
POLL_INTERVAL = 60  # 초


def _ncp_headers(method: str, path: str) -> dict:
    ts = str(int(time.time() * 1000))
    msg = f"{method} {path}\n{ts}\n{settings.ncp_access_key}"
    sig = base64.b64encode(
        hmac.new(settings.ncp_secret_key.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "x-ncp-apigw-timestamp": ts,
        "x-ncp-iam-access-key": settings.ncp_access_key,
        "x-ncp-apigw-signature-v2": sig,
        "Content-Type": "application/json",
    }


async def fetch_events(start_ms: int, end_ms: int) -> list[dict]:
    path = "/cw_fea/real/cw/api/event/search"
    body = {"startTime": str(start_ms // 1000), "endTime": str(end_ms // 1000)}
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{CI_BASE}{path}",
            headers=_ncp_headers("POST", path),
            json=body,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    return data.get("events") or []


def _event_to_alarm_payload(event: dict) -> dict:
    """Cloud Insight 이벤트를 webhook 페이로드 형식으로 변환."""
    start_ms = event.get("startTime", 0)
    alarm_time = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat()

    metric = event.get("metric", "")
    metric_type = "cpu"
    for m in ("cpu", "memory", "disk", "network"):
        if m in metric.lower():
            metric_type = m
            break

    return {
        "alarmId":       str(event.get("eventId", "")),
        "alarmName":     event.get("ruleName", "Cloud Insight Alert"),
        "resourceName":  event.get("resourceName", ""),
        "metricType":    metric_type,
        "metric":        metric,
        "threshold":     event.get("criteria", ""),
        "currentValue":  event.get("detectValue", ""),
        "alarmTime":     alarm_time,
        "eventLevel":    event.get("eventLevel", ""),
        "regionCode":    event.get("regionCode", ""),
        "instanceNo":    (event.get("dimension") or {}).get("instanceNo", ""),
        "_raw_event":    event,
    }


class NCPPoller:
    def __init__(self):
        self._last_poll_ms: int = int(time.time() * 1000) - POLL_INTERVAL * 1000
        self._seen_event_ids: set[str] = set()
        self._running = False

    async def start(self):
        if not (settings.ncp_access_key and settings.ncp_secret_key):
            logger.info("NCP 키 미설정 — 폴러 비활성화 (Mock 모드)")
            return
        self._running = True
        logger.info("NCP 폴러 시작 (주기: %ds)", POLL_INTERVAL)
        asyncio.create_task(self._loop())

    def stop(self):
        self._running = False

    async def _loop(self):
        while self._running:
            try:
                await self._poll_once()
            except Exception as e:
                logger.warning("폴링 오류 (다음 주기 재시도): %s", e)
            await asyncio.sleep(POLL_INTERVAL)

    async def _poll_once(self):
        now_ms = int(time.time() * 1000)
        # 마지막 폴링 시점 ~ 현재 (최대 2분 윈도우)
        start_ms = max(self._last_poll_ms, now_ms - 120_000)

        events = await fetch_events(start_ms, now_ms)
        self._last_poll_ms = now_ms

        new_events = [
            e for e in events
            if str(e.get("eventId", "")) not in self._seen_event_ids
        ]

        if not new_events:
            logger.debug("새 이벤트 없음")
            return

        logger.info("새 Cloud Insight 이벤트 %d건 감지", len(new_events))
        for event in new_events:
            self._seen_event_ids.add(str(event.get("eventId", "")))
            payload = _event_to_alarm_payload(event)
            await self._handle_event(payload)

        # 메모리 누수 방지: 오래된 이벤트 ID 정리 (1000개 초과 시)
        if len(self._seen_event_ids) > 1000:
            self._seen_event_ids = set(list(self._seen_event_ids)[-500:])

    async def _handle_event(self, payload: dict):
        from src.db.database import get_db as _get_db
        from src.db.crud import create_incident
        from src.webhook.handler import run_pipeline

        db = next(_get_db())
        try:
            incident = create_incident(db, payload)
            logger.info(
                "인시던트 생성: id=%d alarm=%s resource=%s",
                incident.id, payload.get("alarmName"), payload.get("resourceName"),
            )
        finally:
            db.close()

        asyncio.create_task(run_pipeline(incident.id, payload))


poller = NCPPoller()
