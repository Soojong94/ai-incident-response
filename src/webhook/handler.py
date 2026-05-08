import hashlib
import hmac
import logging
from datetime import datetime

from fastapi import HTTPException, Request

from src.config import settings
from src.db.crud import create_incident, create_analysis, update_incident_status, add_logs
from src.db.database import get_db as _get_db

logger = logging.getLogger(__name__)


def verify_hmac(body: bytes, signature: str) -> bool:
    if not settings.webhook_secret:
        return True
    expected = hmac.new(settings.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def receive_alarm(request: Request, payload: dict, db) -> dict:
    body_bytes = await request.body()
    sig = request.headers.get("x-ncp-apigw-signature-v2", "")
    if not verify_hmac(body_bytes, sig):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    incident = create_incident(db, payload)
    return {"incident_id": incident.id, "status": incident.status}


async def run_pipeline(incident_id: int, alarm_data: dict) -> None:
    """Background task: collect logs → analyze → store result."""
    from src.collector import mock_collector, ncp_collector, obs_collector

    db = next(_get_db())
    try:
        logs: list[str] = []
        log_source = "mock"

        obs_key = alarm_data.get("obs_object_key") or alarm_data.get("obsObjectKey")
        obs_bucket = alarm_data.get("obs_bucket") or alarm_data.get("obsBucket") or settings.obs_bucket

        if obs_key and settings.ncp_access_key and settings.ncp_secret_key:
            try:
                logs = await obs_collector.collect(obs_bucket, obs_key)
                log_source = "obs"
            except Exception as e:
                logger.warning("OBS log collection failed (%s), falling back to CLA/mock", e)

        if not logs and settings.ncp_access_key and settings.ncp_secret_key:
            try:
                logs = await ncp_collector.collect(alarm_data)
                log_source = "ncp_api"
            except Exception as e:
                logger.warning("NCP log collection failed (%s), falling back to mock", e)

        if not logs:
            logs = await mock_collector.collect(alarm_data)
            log_source = "mock"

        add_logs(db, incident_id, logs, source=log_source)

        from src.analyzer.ai_client import ai_client
        try:
            logger.info("AI 분석 요청 (incident=%d) — 진행 중인 분석 있으면 큐 대기", incident_id)
            result = await ai_client.analyze(alarm_data, logs)
            create_analysis(db, incident_id, result)
            update_incident_status(db, incident_id, "analyzed", severity=result.get("severity"))
            from src.notifier.email_notifier import send_analysis_complete
            send_analysis_complete(incident_id, alarm_data.get("alarmName", "알람"), result)
        except Exception as e:
            logger.error("AI analysis failed for incident %d: %s", incident_id, e)
            update_incident_status(db, incident_id, "ai_failed")
    finally:
        db.close()
