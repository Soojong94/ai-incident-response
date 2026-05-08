import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.config import settings

logger = logging.getLogger(__name__)

DASHBOARD_URL = "https://tbit-msp.kro.kr"


def send_analysis_complete(incident_id: int, alarm_name: str, analysis: dict) -> None:
    if not settings.alert_email or not settings.smtp_user:
        logger.info("SMTP 미설정 — 이메일 발송 생략")
        return

    severity = analysis.get("severity", "-")
    category = analysis.get("cause_category", "-")
    detail = analysis.get("cause_detail", "-")
    actions = analysis.get("immediate_actions", [])
    confidence = analysis.get("confidence", "-")

    severity_color = {
        "Critical": "#e53e3e",
        "High": "#dd6b20",
        "Medium": "#d69e2e",
        "Low": "#38a169",
    }.get(severity, "#718096")

    actions_html = "".join(f"<li style='margin-bottom:8px;'>{a}</li>" for a in actions[:3])

    html = f"""
<html><body style="font-family:sans-serif; background:#f7fafc; padding:20px;">
<div style="max-width:600px; margin:0 auto; background:#fff; border-radius:8px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,0.1);">
  <div style="background:#1a202c; padding:20px 24px;">
    <h2 style="color:#fff; margin:0; font-size:18px;">AI 장애 대응 시스템</h2>
    <p style="color:#a0aec0; margin:4px 0 0; font-size:13px;">분석 완료 알림</p>
  </div>
  <div style="padding:24px;">
    <h3 style="margin:0 0 4px; font-size:16px;">장애 #{incident_id} — {alarm_name}</h3>
    <p style="margin:0 0 20px;">
      <span style="background:{severity_color}; color:#fff; padding:3px 10px; border-radius:4px; font-size:13px; font-weight:bold;">{severity}</span>
      &nbsp;
      <span style="color:#718096; font-size:13px;">{category}</span>
    </p>

    <h4 style="color:#2d3748; margin:0 0 8px;">원인 요약</h4>
    <p style="color:#4a5568; font-size:14px; line-height:1.6; margin:0 0 20px;">{detail[:300]}{'...' if len(detail) > 300 else ''}</p>

    <h4 style="color:#2d3748; margin:0 0 8px;">즉시 조치 사항</h4>
    <ul style="color:#4a5568; font-size:14px; line-height:1.6; margin:0 0 20px; padding-left:20px;">
      {actions_html}
    </ul>

    <p style="color:#718096; font-size:12px; margin:0 0 16px;">신뢰도: {confidence}</p>

    <a href="{DASHBOARD_URL}/incidents/{incident_id}"
       style="display:inline-block; background:#3182ce; color:#fff; padding:10px 20px; border-radius:6px; text-decoration:none; font-size:14px;">
      상세 분석 보기 →
    </a>
  </div>
</div>
</body></html>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[AI장애대응] #{incident_id} {alarm_name} — {severity} 분석 완료"
    msg["From"] = settings.smtp_user
    msg["To"] = settings.alert_email
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_user, settings.alert_email, msg.as_string())
        logger.info("이메일 발송 완료: incident=%d → %s", incident_id, settings.alert_email)
    except Exception as e:
        logger.error("이메일 발송 실패: %s", e)
