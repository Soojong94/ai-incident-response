import fnmatch
import logging
import uuid
from datetime import datetime
from sqlalchemy.orm import Session
from src.db.models import Incident, IncidentLog, AnalysisResult, Recipient, Site, User, NotificationLog

logger = logging.getLogger(__name__)


SEVERITY_FIELD = {
    "Critical": "receive_critical",
    "High": "receive_high",
    "Medium": "receive_medium",
    "Low": "receive_low",
}


def _parse_alarm(alarm_data: dict) -> dict:
    """Normalize NCP Cloud Insight webhook payload (camelCase or snake_case)."""
    def get(*keys):
        for k in keys:
            v = alarm_data.get(k)
            if v is not None:
                return v
        return None

    raw_time = get("alarmTime", "alarm_time")
    try:
        alarm_time = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
    except Exception:
        alarm_time = datetime.utcnow()

    return {
        "alarm_id": get("alarmId", "alarm_id") or str(uuid.uuid4()),
        "alarm_name": get("alarmName", "alarm_name") or "Unknown Alarm",
        "resource_name": get("resourceName", "resource_name") or "Unknown Resource",
        "metric_type": get("metricType", "metric_type") or "Unknown",
        "threshold_value": str(get("threshold", "threshold_value") or ""),
        "current_value": str(get("currentValue", "current_value") or ""),
        "alarm_time": alarm_time,
        "obs_bucket": get("obs_bucket", "obsBucket") or "",
        "obs_object_key": get("obs_object_key", "obsObjectKey") or "",
    }


def _match_or_create_site(db: Session, resource_name: str, obs_bucket: str | None) -> Site | None:
    """resource_name / obs_bucket 으로 매칭 → 없으면 자동 생성."""
    sites = db.query(Site).order_by(Site.id.asc()).all()
    # 1차: obs_bucket 정확 일치
    if obs_bucket:
        for s in sites:
            if s.obs_bucket and s.obs_bucket == obs_bucket:
                return s
    # 2차: resource_name이 글로브 패턴에 매치
    if resource_name:
        for s in sites:
            if s.resource_pattern and fnmatch.fnmatchcase(resource_name.lower(), s.resource_pattern.lower()):
                return s
    # 매칭 실패 → 자동 생성 (admin이 나중에 이름/패턴 다듬을 수 있게 auto_created=True)
    if not resource_name and not obs_bucket:
        return None  # 너무 빈약하면 안 만듦
    base_name = resource_name or f"obs:{obs_bucket}"
    name = base_name
    suffix = 1
    while db.query(Site).filter(Site.name == name).first():
        suffix += 1
        name = f"{base_name} ({suffix})"
    site = Site(
        name=name,
        description="알람 페이로드로 자동 생성됨 — 이름/패턴/수신자를 다듬어 주세요.",
        resource_pattern=resource_name or None,
        obs_bucket=obs_bucket or None,
        auto_created=True,
        enabled=True,
    )
    db.add(site)
    db.commit()
    db.refresh(site)
    logger.info("사이트 자동 생성: %s (resource=%s, bucket=%s)", site.name, resource_name, obs_bucket)
    return site


def create_incident(db: Session, alarm_data: dict) -> Incident:
    parsed = _parse_alarm(alarm_data)
    site = _match_or_create_site(db, parsed["resource_name"], parsed.get("obs_bucket"))
    incident = Incident(
        **parsed,
        site_id=site.id if site else None,
        raw_alarm=alarm_data,
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return incident


def get_incident(db: Session, incident_id: int) -> Incident | None:
    return db.query(Incident).filter(Incident.id == incident_id).first()


def _incidents_query(db: Session, search: str | None = None):
    q = db.query(Incident)
    if search:
        like = f"%{search}%"
        from sqlalchemy import or_
        q = q.filter(or_(
            Incident.alarm_name.ilike(like),
            Incident.resource_name.ilike(like),
            Incident.metric_type.ilike(like),
            Incident.severity.ilike(like),
        ))
    return q


def get_incidents(db: Session, skip: int = 0, limit: int = 100, search: str | None = None) -> list[Incident]:
    return (
        _incidents_query(db, search)
        .order_by(Incident.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def count_incidents(db: Session, search: str | None = None) -> int:
    return _incidents_query(db, search).count()


def add_logs(db: Session, incident_id: int, logs: list[str], source: str = "mock") -> None:
    for line in logs:
        db.add(IncidentLog(incident_id=incident_id, source=source, log_content=line))
    db.commit()


def create_analysis(db: Session, incident_id: int, data: dict) -> AnalysisResult:
    existing = db.query(AnalysisResult).filter(AnalysisResult.incident_id == incident_id).first()
    if existing:
        return existing
    analysis = AnalysisResult(
        incident_id=incident_id,
        cause_category=data.get("cause_category"),
        cause_detail=data.get("cause_detail"),
        severity=data.get("severity"),
        impact_scope=data.get("impact_scope"),
        immediate_actions=data.get("immediate_actions", []),
        prevention=data.get("prevention"),
        confidence=data.get("confidence"),
        raw_response=data.get("raw_response"),
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return analysis


def delete_incident(db: Session, incident_id: int) -> bool:
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        return False
    db.delete(incident)
    db.commit()
    return True


def delete_all_incidents(db: Session) -> int:
    count = db.query(Incident).count()
    db.query(Incident).delete()
    db.commit()
    return count


def update_incident_status(db: Session, incident_id: int, status: str, severity: str | None = None) -> None:
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if incident:
        incident.status = status
        if severity:
            incident.severity = severity
        incident.updated_at = datetime.utcnow()
        db.commit()


# ── Recipient CRUD ──────────────────────────────────────────────────────────

def list_recipients(db: Session) -> list[Recipient]:
    return db.query(Recipient).order_by(Recipient.created_at.desc()).all()


def get_recipient(db: Session, recipient_id: int) -> Recipient | None:
    return db.query(Recipient).filter(Recipient.id == recipient_id).first()


def create_recipient(db: Session, data: dict) -> Recipient:
    recipient = Recipient(
        site_id=data.get("site_id"),
        name=data.get("name", ""),
        email=data.get("email") or None,
        slack_webhook=data.get("slack_webhook") or None,
        receive_critical=bool(data.get("receive_critical", True)),
        receive_high=bool(data.get("receive_high", True)),
        receive_medium=bool(data.get("receive_medium", False)),
        receive_low=bool(data.get("receive_low", False)),
        enabled=bool(data.get("enabled", True)),
    )
    db.add(recipient)
    db.commit()
    db.refresh(recipient)
    return recipient


def update_recipient(db: Session, recipient_id: int, data: dict) -> Recipient | None:
    recipient = get_recipient(db, recipient_id)
    if not recipient:
        return None
    if "site_id" in data:
        recipient.site_id = data["site_id"]
    for field in ("name", "email", "slack_webhook"):
        if field in data:
            setattr(recipient, field, data[field] or None)
    for field in ("receive_critical", "receive_high", "receive_medium", "receive_low", "enabled"):
        if field in data:
            setattr(recipient, field, bool(data[field]))
    recipient.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(recipient)
    return recipient


def delete_recipient(db: Session, recipient_id: int) -> bool:
    recipient = get_recipient(db, recipient_id)
    if not recipient:
        return False
    db.delete(recipient)
    db.commit()
    return True


def get_recipients_for_severity(db: Session, severity: str, site_id: int | None = None) -> list[Recipient]:
    """주어진 severity를 받기로 한 활성 수신자 목록.
    site_id가 주어지면 해당 사이트 소속 수신자만, 없으면 전체 활성 수신자."""
    field = SEVERITY_FIELD.get(severity)
    if not field:
        return []
    q = (
        db.query(Recipient)
        .outerjoin(Site, Recipient.site_id == Site.id)
        .filter(Recipient.enabled.is_(True))
        .filter(getattr(Recipient, field).is_(True))
        .filter((Site.enabled.is_(True)) | (Recipient.site_id.is_(None)))
    )
    if site_id is not None:
        q = q.filter(Recipient.site_id == site_id)
    return q.all()


# ── Site CRUD ──────────────────────────────────────────────────────────────

def list_sites(db: Session) -> list[Site]:
    return db.query(Site).order_by(Site.created_at.desc()).all()


def get_site(db: Session, site_id: int) -> Site | None:
    return db.query(Site).filter(Site.id == site_id).first()


def create_site(db: Session, data: dict) -> Site:
    site = Site(
        name=data.get("name", ""),
        description=data.get("description") or None,
        resource_pattern=data.get("resource_pattern") or None,
        obs_bucket=data.get("obs_bucket") or None,
        architecture=data.get("architecture") or None,
        enabled=bool(data.get("enabled", True)),
    )
    db.add(site)
    db.commit()
    db.refresh(site)
    return site


def update_site(db: Session, site_id: int, data: dict) -> Site | None:
    site = get_site(db, site_id)
    if not site:
        return None
    if "name" in data and data["name"]:
        site.name = data["name"]
    if "description" in data:
        site.description = data["description"] or None
    if "resource_pattern" in data:
        site.resource_pattern = data["resource_pattern"] or None
    if "obs_bucket" in data:
        site.obs_bucket = data["obs_bucket"] or None
    if "architecture" in data:
        site.architecture = data["architecture"] or None
    if "enabled" in data:
        site.enabled = bool(data["enabled"])
    site.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(site)
    return site


def delete_site(db: Session, site_id: int) -> bool:
    site = get_site(db, site_id)
    if not site:
        return False
    db.delete(site)  # cascade로 소속 수신자도 함께 삭제
    db.commit()
    return True


def list_recipients_for_site(db: Session, site_id: int) -> list[Recipient]:
    return (
        db.query(Recipient)
        .filter(Recipient.site_id == site_id)
        .order_by(Recipient.created_at.asc())
        .all()
    )


# ── User CRUD ──────────────────────────────────────────────────────────────

def get_user(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email.lower()).first()


def list_users(db: Session) -> list[User]:
    return db.query(User).order_by(User.created_at.asc()).all()


def create_user(db: Session, email: str, password_hash: str, role: str = "viewer", name: str | None = None) -> User:
    user = User(
        email=email.lower(),
        password_hash=password_hash,
        name=name,
        role=role if role in ("admin", "viewer") else "viewer",
        enabled=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_user(db: Session, user_id: int, data: dict) -> User | None:
    user = get_user(db, user_id)
    if not user:
        return None
    if "name" in data:
        user.name = data["name"] or None
    if "role" in data and data["role"] in ("admin", "viewer"):
        user.role = data["role"]
    if "enabled" in data:
        user.enabled = bool(data["enabled"])
    if "password_hash" in data and data["password_hash"]:
        user.password_hash = data["password_hash"]
    user.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(user)
    return user


def delete_user(db: Session, user_id: int) -> bool:
    user = get_user(db, user_id)
    if not user:
        return False
    db.delete(user)
    db.commit()
    return True


def touch_user_login(db: Session, user_id: int) -> None:
    user = get_user(db, user_id)
    if user:
        user.last_login_at = datetime.utcnow()
        db.commit()


# ── NotificationLog ────────────────────────────────────────────────────────

def record_notification(
    db: Session,
    incident_id: int,
    recipient_id: int | None,
    recipient_label: str,
    channel: str,
    status: str,
    error_message: str | None = None,
) -> NotificationLog:
    log = NotificationLog(
        incident_id=incident_id,
        recipient_id=recipient_id,
        recipient_label=recipient_label[:300] if recipient_label else "",
        channel=channel,
        status=status,
        error_message=error_message,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def list_notifications_for_incident(db: Session, incident_id: int) -> list[NotificationLog]:
    return (
        db.query(NotificationLog)
        .filter(NotificationLog.incident_id == incident_id)
        .order_by(NotificationLog.sent_at.asc())
        .all()
    )
