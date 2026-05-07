"""
NCP Cloud Log Analytics (CLA) 기반 로그 수집기.
SearchLogs API: POST https://cloudloganalytics.apigw.ntruss.com/api/{regionCode}-v1/logs/search
"""
import base64
import hashlib
import hmac
import logging
import time
from datetime import datetime, timedelta

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

CLA_BASE = "https://cloudloganalytics.apigw.ntruss.com"
REGION_CODE = "kr"


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


async def collect(alarm_data: dict) -> list[str]:
    alarm_time_raw = alarm_data.get("alarm_time")
    try:
        alarm_time = datetime.fromisoformat(str(alarm_time_raw)) if alarm_time_raw else datetime.now()
    except (ValueError, TypeError):
        alarm_time = datetime.now()

    start_ts = int((alarm_time - timedelta(minutes=5)).timestamp())
    end_ts = int(alarm_time.timestamp())
    resource = alarm_data.get("resource_name", "")

    path = f"/api/{REGION_CODE}-v1/logs/search"
    body = {
        "timestampFrom": str(start_ts),
        "timestampTo": str(end_ts),
        "keyword": resource,
        "pageNo": 1,
        "pageSize": 100,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{CLA_BASE}{path}",
            headers=_ncp_headers("POST", path),
            json=body,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("result", {}).get("searchResult", [])
    logs = [
        f"{r.get('logTime', '')} [{r.get('logType', '')}] {r.get('servername', '')} {r.get('logDetail', '')}"
        for r in results
    ]
    logger.info("CLA collected %d log lines for resource=%s", len(logs), resource)
    return logs
