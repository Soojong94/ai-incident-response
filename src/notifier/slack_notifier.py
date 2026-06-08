"""Slack Incoming Webhook 발송 — severity 색상 + blocks 카드형 메시지."""
import logging

import httpx

logger = logging.getLogger(__name__)

DASHBOARD_URL = "https://tbit-msp.kro.kr"

SEVERITY_COLOR = {
    "Critical": "#e53e3e",
    "High": "#dd6b20",
    "Medium": "#d69e2e",
    "Low": "#38a169",
}


def _action_buttons(incident_id: int, ack_token: str | None) -> list:
    """ack_token이 있으면 '확인'(에스컬레이션 중지) 버튼 + 상세보기. 없으면 상세보기만."""
    btns = []
    if ack_token:
        btns.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "✓ 확인 (에스컬레이션 중지)"},
            "url": f"{DASHBOARD_URL}/ack/{ack_token}?by=Slack",
            "style": "primary",
        })
    detail = {
        "type": "button",
        "text": {"type": "plain_text", "text": "상세 분석 보기 →"},
        "url": f"{DASHBOARD_URL}/incidents/{incident_id}",
    }
    if not ack_token:
        detail["style"] = "primary"   # ack 버튼 없을 때만 상세보기를 강조색으로
    btns.append(detail)
    return btns


def _build_payload(incident_id: int, alarm_name: str, analysis: dict, ack_token: str | None = None,
                   host: str = "", group: str = "") -> dict:
    severity = analysis.get("severity", "-")
    category = analysis.get("cause_category", "-")
    detail = analysis.get("cause_detail", "-")
    actions = analysis.get("immediate_actions", []) or []
    confidence = analysis.get("confidence", "-")
    color = SEVERITY_COLOR.get(severity, "#718096")

    actions_text = "\n".join(f"• {a}" for a in actions[:5]) or "_없음_"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🚨 [{severity}] {alarm_name}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"🏢 조직 *{group or '-'}*  ·  🖥 서버 *{host or '-'}*"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Incident*\n#{incident_id}"},
                {"type": "mrkdwn", "text": f"*분류*\n{category}"},
                {"type": "mrkdwn", "text": f"*심각도*\n{severity}"},
                {"type": "mrkdwn", "text": f"*신뢰도*\n{confidence}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*원인 요약*\n{detail[:500]}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*즉시 조치 사항*\n{actions_text}"},
        },
        {
            "type": "actions",
            "elements": _action_buttons(incident_id, ack_token),
        },
    ]

    return {
        "text": f"[{severity}] {alarm_name} — Incident #{incident_id}",
        "attachments": [{"color": color, "blocks": blocks}],
    }


def send_ack_notice(webhook_url: str, incident_id: int, by: str, host: str = "", group: str = "") -> tuple[bool, str | None]:
    """장애가 확인(ack)됐음을 Slack 채널에 후속 메시지로 알림 — '누가 확인했는지' 표시용."""
    if not webhook_url:
        return False, "webhook URL 없음"
    loc = f"{group}/{host}" if group else (host or "")
    text = f"✅ 장애 #{incident_id}{' [' + loc + ']' if loc else ''} *확인됨* — by *{by or '익명'}* (에스컬레이션 중지)"
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(webhook_url, json={"text": text})
        if resp.status_code >= 400:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        return True, None
    except Exception as e:
        return False, str(e)[:500]


def send_slack(webhook_url: str, incident_id: int, alarm_name: str, analysis: dict, ack_token: str | None = None,
               host: str = "", group: str = "") -> tuple[bool, str | None]:
    """반환: (성공 여부, 실패 시 사유). ack_token이 있으면 '확인'(에스컬레이션 중지) 버튼 포함.
    host/group이 있으면 카드에 조직/서버 표시."""
    if not webhook_url:
        return False, "webhook URL 없음"
    payload = _build_payload(incident_id, alarm_name, analysis, ack_token=ack_token, host=host, group=group)
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(webhook_url, json=payload)
        if resp.status_code >= 400:
            err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            logger.error("Slack 발송 실패 — %s", err)
            return False, err
        logger.info("Slack 발송 완료: incident=%d", incident_id)
        return True, None
    except Exception as e:
        logger.error("Slack 발송 예외: %s", e)
        return False, str(e)[:500]
