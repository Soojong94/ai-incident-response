"""
에이전트 기반 PoC — 중앙 브리지.

vmalert(Alertmanager v2 포맷)에서 알람을 수신 →
VictoriaLogs에서 해당 host의 알람 직전 N분 로그를 LogsQL로 조회 →
obs_collector가 기대하는 JSONL(`@timestamp`/`type`/`message`)로 패키징 →
중앙 OBS(tbit-air)에 **같은 계정**으로 PUT.

그 뒤는 기존 그대로(무변경): OBS Object Created → 중앙 CF(obs-to-webhook)
→ /webhook/alarm → 서버가 OBS 다운로드 → claude 분석 → 대시보드/이메일.

객체 키: <resource_name>/CLA-<unix_ts>/result.json  (첫 세그먼트=resource_name → CF가 prefix 매칭)
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
import httpx
from botocore.config import Config
from fastapi import FastAPI, Request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("bridge")

VICTORIALOGS_URL = os.environ["VICTORIALOGS_URL"].rstrip("/")
OBS_ENDPOINT = os.environ.get("OBS_ENDPOINT", "https://kr.object.ncloudstorage.com")
OBS_BUCKET = os.environ.get("OBS_BUCKET", "tbit-air")
NCP_ACCESS_KEY = os.environ.get("NCP_ACCESS_KEY", "")
NCP_SECRET_KEY = os.environ.get("NCP_SECRET_KEY", "")
LOG_WINDOW_SECONDS = int(os.environ.get("LOG_WINDOW_SECONDS", "300"))

app = FastAPI(title="air-poc-bridge")

# boto3 1.36+ 기본 streaming 체크섬을 NCP OBS가 거부(SHA256_Header_Mismatch) → when_required로 끔 (학습된 이슈)
_BOTO_CONFIG = Config(
    request_checksum_calculation="when_required",
    response_checksum_validation="when_required",
)


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=OBS_ENDPOINT,
        aws_access_key_id=NCP_ACCESS_KEY,
        aws_secret_access_key=NCP_SECRET_KEY,
        config=_BOTO_CONFIG,
    )


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


async def query_logs(host: str, end: datetime) -> list[dict]:
    """VictoriaLogs에서 host 스트림의 [end-N분, end] 구간 로그를 NDJSON으로 조회."""
    start = end - timedelta(seconds=LOG_WINDOW_SECONDS)
    query = f'{{host={json.dumps(host)}}} _time:[{_rfc3339(start)}, {_rfc3339(end)}]'
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{VICTORIALOGS_URL}/select/logsql/query",
            data={"query": query},
        )
        resp.raise_for_status()
        records = []
        for raw in resp.text.splitlines():
            raw = raw.strip()
            if raw:
                try:
                    records.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        return records


def _to_jsonl(records: list[dict]) -> str:
    """VictoriaLogs 레코드 → obs_collector가 기대하는 JSONL."""
    lines = []
    for r in records:
        lines.append(json.dumps({
            "@timestamp": r.get("_time", ""),
            "type": r.get("job") or r.get("filename") or "alloy",
            "message": r.get("_msg", ""),
        }, ensure_ascii=False))
    return "\n".join(lines)


def _upload(key: str, body: str) -> None:
    _s3().put_object(
        Bucket=OBS_BUCKET,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="application/json",
        # 같은 계정 업로드 → cross-account GrantFullControl 불필요
    )


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/api/v2/alerts")
async def receive_alerts(request: Request):
    """vmalert가 Alertmanager v2 포맷(알람 배열)으로 POST."""
    alerts = await request.json()
    if isinstance(alerts, dict):  # 방어적 — 일부 포맷은 {"alerts":[...]}
        alerts = alerts.get("alerts", [])

    uploaded = []
    for alert in alerts:
        if alert.get("status") == "resolved":
            continue
        labels = alert.get("labels", {})
        host = labels.get("host") or labels.get("instance")
        if not host:
            logger.warning("host 라벨 없는 알람 skip: labels=%s", labels)
            continue

        end = _parse_time(alert.get("startsAt")) or datetime.now(timezone.utc)
        records = await query_logs(host, end)
        body = _to_jsonl(records)  # 0줄이어도 업로드 → 가용 정보로 분석 진행(graceful degradation)
        key = f"{host}/CLA-{int(end.timestamp())}/result.json"
        await asyncio.to_thread(_upload, key, body)
        logger.info("업로드 완료 host=%s key=%s lines=%d", host, key, len(records))
        uploaded.append({"host": host, "key": key, "lines": len(records)})

    return {"uploaded": uploaded}
