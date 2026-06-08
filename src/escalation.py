"""에스컬레이션 스케줄러.

escalation_enabled 사이트의 미확인(ack 안 된) 장애를, 직전 발송 후 지연(분)이 지나면
다음 단계(escalation_level+1, 수신자 있는 최소 단계) 수신자에게 추가 발송한다.
확인(ack)되거나 최고 단계에 도달하면 멈춘다. (메일의 '확인' 버튼 = /ack/{token})
"""
import asyncio
import logging
from datetime import datetime

from src.config import settings
from src.db.database import SessionLocal
from src.db.crud import (
    get_incidents_to_escalate, get_recipients_for_level, max_escalation_level,
    record_notification,
)

logger = logging.getLogger(__name__)


def _analysis_dict(inc) -> dict:
    a = inc.analysis_result
    return {
        "severity": (a.severity if a else inc.severity) or "-",
        "cause_category": a.cause_category if a else "-",
        "cause_detail": a.cause_detail if a else "-",
        "impact_scope": a.impact_scope if a else "-",
        "immediate_actions": (a.immediate_actions if a else []) or [],
        "confidence": a.confidence if a else "-",
    }


def _notify_level(db, inc, level: int, recips) -> None:
    from src.notifier.email_notifier import send_analysis_complete
    from src.notifier.slack_notifier import send_slack
    adict = _analysis_dict(inc)
    name = inc.alarm_name or "알람"
    host = inc.resource_name or ""
    group = (inc.site.group_name if inc.site else "") or ""
    for r in recips:
        if r.email:
            ok, err = send_analysis_complete(inc.id, name, adict, recipient_email=r.email, ack_token=inc.ack_token, host=host, group=group)
            record_notification(db, inc.id, recipient_id=r.id, recipient_label=f"[esc L{level}] {r.name} <{r.email}>",
                                channel="email", status="sent" if ok else "failed", error_message=err)
        if r.slack_webhook:
            ok, err = send_slack(r.slack_webhook, inc.id, name, adict, ack_token=inc.ack_token)
            record_notification(db, inc.id, recipient_id=r.id, recipient_label=f"[esc L{level}] {r.name} (Slack)",
                                channel="slack", status="sent" if ok else "failed", error_message=err)


def _next_level(db, site_id: int, cur: int, maxlvl: int):
    """cur 다음으로 수신자가 있는 단계. top을 넘으면 0부터 다시 순환(repeat).
    한 바퀴(0..max) 다 돌고도 미확인이면 0으로 wrap → 다시 반복."""
    order = list(range(cur + 1, maxlvl + 1)) + list(range(0, cur + 1))
    for lvl in order:
        if get_recipients_for_level(db, site_id, lvl):
            return lvl
    return None


def check_escalations_once() -> None:
    db = SessionLocal()
    try:
        for inc in get_incidents_to_escalate(db):
            maxlvl = max_escalation_level(db, inc.site_id)
            cur = int(inc.escalation_level or 0)
            level = _next_level(db, inc.site_id, cur, maxlvl)
            if level is None:
                continue  # 단계 수신자 없음
            recips = get_recipients_for_level(db, inc.site_id, level)
            _notify_level(db, inc, level, recips)
            inc.escalation_level = level
            inc.last_escalated_at = datetime.now()
            db.commit()
            cycled = " [재순환]" if level <= cur else ""
            logger.warning("에스컬레이션: incident=%d → level %d (%d명)%s", inc.id, level, len(recips), cycled)
    finally:
        db.close()


async def escalation_loop() -> None:
    await asyncio.sleep(30)
    while True:
        try:
            await asyncio.to_thread(check_escalations_once)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("에스컬레이션 점검 실패: %s", e)
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            break
