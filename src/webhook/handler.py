import asyncio
import hashlib
import hmac
import logging
from datetime import datetime

from fastapi import HTTPException, Request

from src.config import settings
from src.db.crud import (
    create_incident, create_analysis, update_incident_status, add_logs,
    get_recipients_for_severity, record_notification, get_incident,
)
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

        # site의 architecture를 prompt 컨텍스트로 사용
        inc = get_incident(db, incident_id)
        site_architecture = inc.site.architecture if inc and inc.site and inc.site.architecture else None

        from src.analyzer.ai_client import ai_client
        try:
            logger.info("AI 분석 요청 (incident=%d, arch=%s)", incident_id, "yes" if site_architecture else "no")
            result = await _analyze_with_retry(ai_client, alarm_data, logs, site_architecture, incident_id)
            analysis = create_analysis(db, incident_id, result)
            update_incident_status(db, incident_id, "analyzed", severity=analysis.severity)
            inc = get_incident(db, incident_id)
            _dispatch_notifications(
                db, incident_id, alarm_data.get("alarmName", "알람"),
                result, analysis.severity,
                site_id=inc.site_id if inc else None,
            )
        except Exception as e:
            logger.error("AI analysis failed for incident %d (재시도 후): %s", incident_id, e)
            db.rollback()
            update_incident_status(db, incident_id, "ai_failed")
    finally:
        db.close()


async def _analyze_with_retry(ai_client, alarm_data: dict, logs: list[str], site_architecture: str | None, incident_id: int) -> dict:
    """AI 분석 1회 자동 재시도 — Timely 504 같은 일시 오류 대응."""
    last_err = None
    for attempt in (1, 2):
        try:
            return await ai_client.analyze(alarm_data, logs, site_architecture=site_architecture)
        except Exception as e:
            last_err = e
            if attempt == 1:
                logger.warning("AI 분석 실패 1회차 (incident=%d): %s — 5초 후 재시도", incident_id, e)
                await asyncio.sleep(5)
            else:
                break
    raise last_err


def _dispatch_notifications(db, incident_id: int, alarm_name: str, analysis: dict, severity: str, site_id: int | None = None) -> None:
    """수신자 테이블 기반으로 이메일/슬랙 발송 + NotificationLog 기록.
    site_id가 있으면 해당 사이트 수신자만, 없거나 비어있으면 .env fallback."""
    from src.notifier.email_notifier import send_analysis_complete
    from src.notifier.slack_notifier import send_slack

    recipients = get_recipients_for_severity(db, severity, site_id=site_id)
    if not recipients:
        logger.info("수신자 테이블 비어있음 — .env fallback (severity=%s)", severity)
        ok, err = send_analysis_complete(incident_id, alarm_name, analysis)
        record_notification(
            db, incident_id,
            recipient_id=None,
            recipient_label=f".env fallback ({settings.alert_email or '미설정'})",
            channel="email",
            status="sent" if ok else "failed",
            error_message=err,
        )
        return

    logger.info("수신자 %d명에게 발송 (severity=%s)", len(recipients), severity)
    for r in recipients:
        if r.email:
            ok, err = send_analysis_complete(incident_id, alarm_name, analysis, recipient_email=r.email)
            record_notification(
                db, incident_id, recipient_id=r.id,
                recipient_label=f"{r.name} <{r.email}>",
                channel="email",
                status="sent" if ok else "failed",
                error_message=err,
            )
        if r.slack_webhook:
            ok, err = send_slack(r.slack_webhook, incident_id, alarm_name, analysis)
            record_notification(
                db, incident_id, recipient_id=r.id,
                recipient_label=f"{r.name} (Slack)",
                channel="slack",
                status="sent" if ok else "failed",
                error_message=err,
            )
