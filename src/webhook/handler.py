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
    find_similar_ai_note, add_site_note, increment_note_occurrence,
    get_site_notes_for_prompt, list_cluster_incidents,
    get_site_ncp_keys, count_recent_incidents_for_site,
    get_recent_analyses_for_resource,
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
    """Background task: collect logs → analyze → store result.
    sites의 rate-limit을 먼저 검사해서 임계값 초과 시 분석 자체를 차단(suppressed).
    비상 모드(rate_limit_disabled=True)일 땐 무시하고 모두 분석."""
    from src.collector import mock_collector, ncp_collector, obs_collector

    db = next(_get_db())
    try:
        # ── Rate limit check ──────────────────────────────────────────────
        inc_for_rl = get_incident(db, incident_id)
        if inc_for_rl and inc_for_rl.site_id and inc_for_rl.site and not inc_for_rl.site.rate_limit_disabled:
            window = inc_for_rl.site.rate_limit_window_seconds or 300
            limit = inc_for_rl.site.rate_limit_count or 3
            recent = count_recent_incidents_for_site(db, inc_for_rl.site_id, window, exclude_id=incident_id)
            # recent에는 자기 자신 제외한 같은 site의 최근 incident 수
            if recent >= limit:
                logger.info(
                    "incident %d — rate-limited (site=%d, recent=%d >= limit=%d, window=%ds) — 분석 skip",
                    incident_id, inc_for_rl.site_id, recent, limit, window,
                )
                update_incident_status(db, incident_id, "suppressed")
                record_notification(
                    db, incident_id,
                    recipient_id=None,
                    recipient_label=f"rate-limited (window={window}s, count>{limit})",
                    channel="rate_limit",
                    status="skipped",
                    error_message=None,
                )
                return

        logs: list[str] = []
        log_source = "mock"

        obs_key = alarm_data.get("obs_object_key") or alarm_data.get("obsObjectKey")
        obs_bucket = alarm_data.get("obs_bucket") or alarm_data.get("obsBucket") or settings.obs_bucket

        # 해당 incident의 site에 NCP 키가 있으면 그걸 사용, 없으면 .env fallback
        _tmp_inc = get_incident(db, incident_id)
        site_id_for_keys = _tmp_inc.site_id if _tmp_inc else None
        ncp_access, ncp_secret = (
            get_site_ncp_keys(db, site_id_for_keys) if site_id_for_keys else
            (settings.ncp_access_key, settings.ncp_secret_key)
        )

        if obs_key and ncp_access and ncp_secret:
            try:
                logs = await obs_collector.collect(obs_bucket, obs_key, access_key=ncp_access, secret_key=ncp_secret)
                log_source = "obs"
            except Exception as e:
                logger.warning("OBS log collection failed (%s), falling back to CLA/mock", e)

        if not logs and ncp_access and ncp_secret:
            try:
                logs = await ncp_collector.collect(alarm_data)
                log_source = "ncp_api"
            except Exception as e:
                logger.warning("NCP log collection failed (%s), falling back to mock", e)

        if not logs:
            logs = await mock_collector.collect(alarm_data)
            log_source = "mock"

        add_logs(db, incident_id, logs, source=log_source)

        # site의 architecture + 누적 메모 + 같은 resource의 과거 분석을 prompt 컨텍스트로 사용
        inc = get_incident(db, incident_id)
        site_architecture = inc.site.architecture if inc and inc.site and inc.site.architecture else None
        site_notes_text: list[str] = []
        if inc and inc.site_id:
            notes = get_site_notes_for_prompt(db, inc.site_id, max_count=8)
            site_notes_text = [
                f"- [{n.author}{' ★ pinned' if n.pinned else ''}{' x'+str(n.occurrences) if n.occurrences and n.occurrences > 1 else ''}] {n.content}"
                for n in notes
            ]
        past_analyses = get_recent_analyses_for_resource(
            db, inc.resource_name if inc else None,
            exclude_incident_id=incident_id, limit=3,
        )

        from src.analyzer.ai_client import ai_client
        try:
            logger.info(
                "AI 분석 요청 (incident=%d, arch=%s, notes=%d, past_analyses=%d)",
                incident_id, "yes" if site_architecture else "no",
                len(site_notes_text), len(past_analyses),
            )
            result = await _analyze_with_retry(
                ai_client, alarm_data, logs, site_architecture, site_notes_text,
                past_analyses, incident_id,
            )
            analysis = create_analysis(db, incident_id, result)
            update_incident_status(db, incident_id, "analyzed", severity=analysis.severity)
            inc = get_incident(db, incident_id)
            # 자동 메모 누적 — 같은 cause_category의 AI 메모가 있으면 카운트, 없으면 신규
            if inc and inc.site_id and result.get("cause_category"):
                _accumulate_site_note(db, inc.site_id, incident_id, result)
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


async def _analyze_with_retry(
    ai_client,
    alarm_data: dict,
    logs: list[str],
    site_architecture: str | None,
    site_notes: list[str] | None,
    past_analyses: list[dict] | None,
    incident_id: int,
) -> dict:
    """AI 분석 1회 자동 재시도 — Timely 504 같은 일시 오류 대응."""
    last_err = None
    for attempt in (1, 2):
        try:
            return await ai_client.analyze(
                alarm_data, logs,
                site_architecture=site_architecture,
                site_notes=site_notes,
                past_analyses=past_analyses,
            )
        except Exception as e:
            last_err = e
            if attempt == 1:
                logger.warning("AI 분석 실패 1회차 (incident=%d): %s — 5초 후 재시도", incident_id, e)
                await asyncio.sleep(5)
            else:
                break
    raise last_err


def _accumulate_site_note(db, site_id: int, incident_id: int, result: dict) -> None:
    """분석 결과에서 메모 한 줄을 추출해 누적.
    같은 cause_category 헤더로 시작하는 AI 메모가 있으면 occurrences를 증가시키고
    3회 도달 시 pinned로 자동 승격. 없으면 새로 추가."""
    cause = (result.get("cause_category") or "").strip()
    detail = (result.get("cause_detail") or "").strip()
    if not cause or not detail:
        return
    # 한 줄 메모 — 사람이 보기 좋게 cause_category 헤더 + detail 첫 문장
    first_sentence = detail.split(". ")[0][:200]
    header = f"[{cause}] "
    content = header + first_sentence
    existing = find_similar_ai_note(db, site_id, header)
    if existing:
        increment_note_occurrence(db, existing.id, threshold=3)
    else:
        add_site_note(
            db,
            site_id=site_id,
            author="ai",
            content=content,
            related_incident_id=incident_id,
        )


def _dispatch_notifications(db, incident_id: int, alarm_name: str, analysis: dict, severity: str, site_id: int | None = None) -> None:
    """수신자 테이블 기반으로 이메일/슬랙 발송 + NotificationLog 기록.
    rate-limit은 run_pipeline 시작 시점에 처리되므로, 여기까지 오면 발송 대상."""
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
