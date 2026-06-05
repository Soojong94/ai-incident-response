import fnmatch
import logging
import uuid
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import Session
from src.db.models import (
    Incident, IncidentLog, AnalysisResult, Recipient, Site, User,
    NotificationLog, PasswordResetToken, SiteNote, AnalysisFeedback,
)

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
        alarm_time = datetime.now()

    return {
        "alarm_id": get("alarmId", "alarm_id") or str(uuid.uuid4()),
        "alarm_name": get("alarmName", "alarm_name") or "Unknown Alarm",
        "resource_name": get("resourceName", "resource_name") or "Unknown Resource",
        "metric_type": get("metricType", "metric_type") or "Unknown",
        "threshold_value": str(get("threshold", "threshold_value") or ""),
        "current_value": str(get("currentValue", "current_value") or ""),
        "alarm_time": alarm_time,
    }


def _match_or_create_site(db: Session, resource_name: str) -> Site | None:
    """resource_name(host) 글로브 패턴으로 매칭 → 없으면 자동 생성."""
    if not resource_name:
        return None
    sites = db.query(Site).order_by(Site.id.asc()).all()
    for s in sites:
        if s.resource_pattern and fnmatch.fnmatchcase(resource_name.lower(), s.resource_pattern.lower()):
            return s
    # 매칭 실패 → 자동 생성 (admin이 나중에 이름/패턴/수신자를 다듬을 수 있게 auto_created=True)
    base_name = resource_name
    name = base_name
    suffix = 1
    while db.query(Site).filter(Site.name == name).first():
        suffix += 1
        name = f"{base_name} ({suffix})"
    site = Site(
        name=name,
        description="수신 데이터로 자동 생성됨 — 이름/패턴/수신자를 다듬어 주세요.",
        resource_pattern=resource_name or None,
        auto_created=True,
        enabled=True,
    )
    db.add(site)
    db.commit()
    db.refresh(site)
    logger.info("사이트 자동 생성: %s (resource=%s)", site.name, resource_name)
    return site


def sync_sites_from_hosts(db: Session, host_groups: list[tuple[str, str | None]]) -> int:
    """[(host, group)] 을 받아, 매칭 사이트가 없으면 생성(패턴 우선 → 없으면 1:1)하고
    group_name(상위 그룹=게이트웨이/공인IP 단위)을 갱신한다. 새로 만든 사이트 수 반환.

    분석/알람 없이도 로그·메트릭이 들어온 서버를 사이트로 바로 올린다."""
    n_before = db.query(Site).count()
    changed = False
    for host, group in host_groups:
        if not host:
            continue
        site = _match_or_create_site(db, host)
        if site and group and site.group_name != group:
            site.group_name = group
            changed = True
    if changed:
        db.commit()
    return db.query(Site).count() - n_before


CLUSTER_WINDOW_SECONDS = 300  # 5분


def _find_or_create_cluster_id(db: Session, site_id: int | None) -> str:
    """같은 site에 최근 5분 안에 만들어진 incident가 있으면 그 cluster_id 재사용, 없으면 새로."""
    if site_id is None:
        return uuid.uuid4().hex
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(seconds=CLUSTER_WINDOW_SECONDS)
    recent = (
        db.query(Incident)
        .filter(Incident.site_id == site_id)
        .filter(Incident.created_at >= cutoff)
        .filter(Incident.cluster_id.isnot(None))
        .order_by(Incident.created_at.desc())
        .first()
    )
    return recent.cluster_id if recent else uuid.uuid4().hex


def create_incident(db: Session, alarm_data: dict) -> Incident:
    parsed = _parse_alarm(alarm_data)
    site = _match_or_create_site(db, parsed["resource_name"])
    site_id = site.id if site else None
    cluster_id = _find_or_create_cluster_id(db, site_id)
    incident = Incident(
        **parsed,
        site_id=site_id,
        cluster_id=cluster_id,
        raw_alarm=alarm_data,
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return incident


def list_cluster_incidents(db: Session, cluster_id: str, exclude_id: int | None = None) -> list[Incident]:
    """같은 클러스터의 incident 목록 (created_at 오래된 순)."""
    q = db.query(Incident).filter(Incident.cluster_id == cluster_id)
    if exclude_id is not None:
        q = q.filter(Incident.id != exclude_id)
    return q.order_by(Incident.created_at.asc()).all()


def count_recent_incidents_for_site(db: Session, site_id: int, window_seconds: int, exclude_id: int | None = None) -> int:
    """site의 최근 window_seconds 안에 만들어진 incident 수."""
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(seconds=window_seconds)
    q = (
        db.query(Incident)
        .filter(Incident.site_id == site_id)
        .filter(Incident.created_at >= cutoff)
    )
    if exclude_id is not None:
        q = q.filter(Incident.id != exclude_id)
    return q.count()


def cluster_has_sent_notification(db: Session, cluster_id: str) -> bool:
    """이 클러스터의 incident 중 이미 알림(NotificationLog)이 발송된 게 있는가?"""
    from sqlalchemy import exists, and_
    q = db.query(NotificationLog).join(Incident, NotificationLog.incident_id == Incident.id).filter(
        Incident.cluster_id == cluster_id,
        NotificationLog.status == "sent",
    )
    return db.query(q.exists()).scalar()


def get_incident(db: Session, incident_id: int) -> Incident | None:
    return db.query(Incident).filter(Incident.id == incident_id).first()


def _incidents_query(
    db: Session,
    search: str | None = None,
    site_id: int | None = None,
    severity: str | None = None,
    status: str | None = None,
    metric_type: str | None = None,
    date_from=None,
    date_to=None,
):
    q = db.query(Incident)
    if site_id is not None:
        q = q.filter(Incident.site_id == site_id)
    if severity:
        q = q.filter(Incident.severity == severity)
    if status:
        q = q.filter(Incident.status == status)
    if metric_type:
        q = q.filter(Incident.metric_type == metric_type)
    if date_from is not None:
        q = q.filter(Incident.alarm_time >= date_from)
    if date_to is not None:
        q = q.filter(Incident.alarm_time <= date_to)
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


def get_incidents(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    search: str | None = None,
    site_id: int | None = None,
    severity: str | None = None,
    status: str | None = None,
    metric_type: str | None = None,
    date_from=None,
    date_to=None,
) -> list[Incident]:
    return (
        _incidents_query(db, search, site_id, severity, status, metric_type, date_from, date_to)
        .order_by(Incident.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def count_incidents(
    db: Session,
    search: str | None = None,
    site_id: int | None = None,
    severity: str | None = None,
    status: str | None = None,
    metric_type: str | None = None,
    date_from=None,
    date_to=None,
) -> int:
    return _incidents_query(db, search, site_id, severity, status, metric_type, date_from, date_to).count()


# ── Site notes ─────────────────────────────────────────────────────────────

def list_site_notes(db: Session, site_id: int, limit: int = 50) -> list[SiteNote]:
    return (
        db.query(SiteNote)
        .filter(SiteNote.site_id == site_id)
        .order_by(SiteNote.pinned.desc(), SiteNote.created_at.desc())
        .limit(limit)
        .all()
    )


def find_similar_ai_note(db: Session, site_id: int, content_prefix: str) -> SiteNote | None:
    """같은 사이트에서 AI가 같은 cause_category 헤더로 적은 노트 찾기 — 누적 카운트용."""
    if not content_prefix:
        return None
    return (
        db.query(SiteNote)
        .filter(SiteNote.site_id == site_id)
        .filter(SiteNote.author == "ai")
        .filter(SiteNote.content.like(content_prefix + "%"))
        .order_by(SiteNote.created_at.desc())
        .first()
    )


def add_site_note(
    db: Session,
    site_id: int,
    author: str,
    content: str,
    user_id: int | None = None,
    related_incident_id: int | None = None,
) -> SiteNote:
    note = SiteNote(
        site_id=site_id,
        author=author if author in ("ai", "user") else "user",
        user_id=user_id,
        content=content,
        related_incident_id=related_incident_id,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def increment_note_occurrence(db: Session, note_id: int, threshold: int = 3) -> SiteNote | None:
    """반복 발생 시 카운터 증가 + threshold 도달 시 pinned=True 자동 승격."""
    note = db.query(SiteNote).filter(SiteNote.id == note_id).first()
    if not note:
        return None
    note.occurrences = (note.occurrences or 1) + 1
    note.updated_at = datetime.now()
    if note.occurrences >= threshold:
        note.pinned = True
    db.commit()
    db.refresh(note)
    return note


def update_site_note(db: Session, note_id: int, data: dict) -> SiteNote | None:
    note = db.query(SiteNote).filter(SiteNote.id == note_id).first()
    if not note:
        return None
    if "content" in data:
        note.content = data["content"]
    if "pinned" in data:
        note.pinned = bool(data["pinned"])
    note.updated_at = datetime.now()
    db.commit()
    db.refresh(note)
    return note


def delete_site_note(db: Session, note_id: int) -> bool:
    note = db.query(SiteNote).filter(SiteNote.id == note_id).first()
    if not note:
        return False
    db.delete(note)
    db.commit()
    return True


def delete_site_notes(db: Session, site_id: int, author: str | None = None) -> int:
    """사이트 메모 일괄 삭제. author 지정 시 그 작성자(ai/user)만, 없으면 전체."""
    q = db.query(SiteNote).filter(SiteNote.site_id == site_id)
    if author:
        q = q.filter(SiteNote.author == author)
    count = q.count()
    q.delete(synchronize_session=False)
    db.commit()
    return count


def get_recent_analyses_for_resource(
    db: Session,
    resource_name: str | None,
    exclude_incident_id: int | None = None,
    limit: int = 3,
) -> list[dict]:
    """같은 resource_name 으로 과거에 분석된 incident 의 분석 결과를 가져옴.
    prompt 컨텍스트 주입용 — 없으면 빈 list 반환."""
    if not resource_name:
        return []
    q = (
        db.query(Incident)
        .filter(Incident.resource_name == resource_name)
        .filter(Incident.status == "analyzed")
        .order_by(Incident.created_at.desc())
    )
    if exclude_incident_id is not None:
        q = q.filter(Incident.id != exclude_incident_id)
    out = []
    for inc in q.limit(limit).all():
        if not inc.analysis_result:
            continue
        a = inc.analysis_result
        out.append({
            "incident_id": inc.id,
            "alarm_time": inc.alarm_time.isoformat() if inc.alarm_time else "",
            "alarm_name": inc.alarm_name or "",
            "severity": a.severity or "",
            "cause_category": a.cause_category or "",
            "cause_detail": (a.cause_detail or "")[:400],
            "prevention": (a.prevention or "")[:250],
        })
    return out


def get_site_notes_for_prompt(db: Session, site_id: int, max_count: int = 8) -> list[SiteNote]:
    """AI prompt 주입용 — pinned 우선, 그 다음 최근 순. 토큰 절약 위해 max_count로 제한."""
    pinned = (
        db.query(SiteNote)
        .filter(SiteNote.site_id == site_id, SiteNote.pinned.is_(True))
        .order_by(SiteNote.updated_at.desc())
        .limit(max_count)
        .all()
    )
    if len(pinned) >= max_count:
        return pinned
    recent = (
        db.query(SiteNote)
        .filter(SiteNote.site_id == site_id, SiteNote.pinned.is_(False))
        .order_by(SiteNote.created_at.desc())
        .limit(max_count - len(pinned))
        .all()
    )
    return pinned + recent


# ── Analysis feedback ──────────────────────────────────────────────────────

def get_feedback_for_incident(db: Session, incident_id: int) -> AnalysisFeedback | None:
    return db.query(AnalysisFeedback).filter(AnalysisFeedback.incident_id == incident_id).first()


def upsert_feedback(db: Session, incident_id: int, user_id: int, rating: str, comment: str | None = None) -> AnalysisFeedback:
    existing = get_feedback_for_incident(db, incident_id)
    if existing:
        existing.rating = rating
        existing.comment = comment
        existing.user_id = user_id
        existing.updated_at = datetime.now()
        db.commit()
        db.refresh(existing)
        return existing
    fb = AnalysisFeedback(incident_id=incident_id, user_id=user_id, rating=rating, comment=comment)
    db.add(fb)
    db.commit()
    db.refresh(fb)
    return fb


def distinct_metric_types(db: Session) -> list[str]:
    """현재 DB의 incident들에 등장하는 metric_type 목록 (필터 dropdown용)."""
    rows = db.query(Incident.metric_type).filter(Incident.metric_type.isnot(None)).distinct().all()
    return sorted({r[0] for r in rows if r[0]})


# ── Statistics (admin 운영 통계용) ─────────────────────────────────────────

def daily_incident_counts(db: Session, days: int = 14) -> list[dict]:
    """지난 N일 간 일별 incident 수 (오래된 → 최신)."""
    from datetime import timedelta
    from sqlalchemy import func
    cutoff = datetime.now() - timedelta(days=days)
    rows = (
        db.query(
            func.date(Incident.created_at).label("d"),
            func.count(Incident.id).label("c"),
        )
        .filter(Incident.created_at >= cutoff)
        .group_by(func.date(Incident.created_at))
        .order_by(func.date(Incident.created_at).asc())
        .all()
    )
    return [{"date": str(r.d), "count": r.c} for r in rows]


def severity_distribution(db: Session, days: int = 30) -> list[dict]:
    from datetime import timedelta
    from sqlalchemy import func
    cutoff = datetime.now() - timedelta(days=days)
    rows = (
        db.query(Incident.severity, func.count(Incident.id).label("c"))
        .filter(Incident.created_at >= cutoff)
        .filter(Incident.severity.isnot(None))
        .group_by(Incident.severity)
        .all()
    )
    return [{"severity": r[0] or "Unknown", "count": r[1]} for r in rows]


def status_distribution(db: Session, days: int = 30) -> list[dict]:
    from datetime import timedelta
    from sqlalchemy import func
    cutoff = datetime.now() - timedelta(days=days)
    rows = (
        db.query(Incident.status, func.count(Incident.id).label("c"))
        .filter(Incident.created_at >= cutoff)
        .group_by(Incident.status)
        .all()
    )
    return [{"status": r[0] or "unknown", "count": r[1]} for r in rows]


def top_sites_by_incident(db: Session, days: int = 30, limit: int = 5) -> list[dict]:
    from datetime import timedelta
    from sqlalchemy import func
    cutoff = datetime.now() - timedelta(days=days)
    rows = (
        db.query(Site.name, func.count(Incident.id).label("c"))
        .join(Incident, Incident.site_id == Site.id)
        .filter(Incident.created_at >= cutoff)
        .group_by(Site.name)
        .order_by(func.count(Incident.id).desc())
        .limit(limit)
        .all()
    )
    return [{"site": r[0], "count": r[1]} for r in rows]


def notification_success_rate(db: Session, days: int = 30) -> dict:
    from datetime import timedelta
    from sqlalchemy import func
    cutoff = datetime.now() - timedelta(days=days)
    rows = (
        db.query(NotificationLog.status, func.count(NotificationLog.id).label("c"))
        .filter(NotificationLog.sent_at >= cutoff)
        .group_by(NotificationLog.status)
        .all()
    )
    by = {r[0]: r[1] for r in rows}
    sent = by.get("sent", 0)
    failed = by.get("failed", 0)
    skipped = by.get("skipped", 0)
    total = sent + failed + skipped
    rate = (sent / total * 100) if total else 0.0
    return {"sent": sent, "failed": failed, "skipped": skipped, "total": total, "success_rate": round(rate, 1)}


def avg_analysis_duration_seconds(db: Session, days: int = 30) -> dict:
    """incident.created_at → incident.updated_at 차이의 평균 (analyzed 인 것만).
    근사치 — incident가 analyzed 되는 시점에 updated_at이 업데이트되기 때문."""
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(days=days)
    rows = (
        db.query(Incident.created_at, Incident.updated_at)
        .filter(Incident.created_at >= cutoff)
        .filter(Incident.status == "analyzed")
        .all()
    )
    if not rows:
        return {"avg_seconds": 0, "count": 0, "under_5min_rate": 0.0}
    deltas = [(u - c).total_seconds() for c, u in rows if c and u]
    if not deltas:
        return {"avg_seconds": 0, "count": 0, "under_5min_rate": 0.0}
    avg = sum(deltas) / len(deltas)
    under = sum(1 for d in deltas if d <= 300)
    rate = under / len(deltas) * 100
    return {
        "avg_seconds": round(avg, 1),
        "count": len(deltas),
        "under_5min_rate": round(rate, 1),
    }


def add_logs(db: Session, incident_id: int, logs: list[str], source: str = "mock") -> None:
    for line in logs:
        db.add(IncidentLog(incident_id=incident_id, source=source, log_content=line))
    db.commit()


def create_analysis(db: Session, incident_id: int, data: dict) -> AnalysisResult:
    """Upsert — 같은 incident_id에 분석이 이미 있으면 새 데이터로 덮어쓴다 (재분석 지원)."""
    existing = db.query(AnalysisResult).filter(AnalysisResult.incident_id == incident_id).first()
    if existing:
        existing.cause_category = data.get("cause_category")
        existing.cause_detail = data.get("cause_detail")
        existing.severity = data.get("severity")
        existing.impact_scope = data.get("impact_scope")
        existing.immediate_actions = data.get("immediate_actions", [])
        existing.prevention = data.get("prevention")
        existing.confidence = data.get("confidence")
        existing.raw_response = data.get("raw_response")
        db.commit()
        db.refresh(existing)
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
    # 이 incident에서 생성된 AI 메모도 함께 삭제 (FK 정리 + 사용자 요청)
    db.query(SiteNote).filter(
        SiteNote.related_incident_id == incident_id,
        SiteNote.author == "ai",
    ).delete(synchronize_session=False)
    db.delete(incident)
    db.commit()
    return True


def delete_all_incidents(db: Session) -> int:
    count = db.query(Incident).count()
    # 장애 내역 전체 삭제 시 AI 메모도 전부 삭제
    db.query(SiteNote).filter(SiteNote.author == "ai").delete(synchronize_session=False)
    db.query(Incident).delete()
    db.commit()
    return count


def update_incident_status(db: Session, incident_id: int, status: str, severity: str | None = None) -> None:
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if incident:
        incident.status = status
        if severity:
            incident.severity = severity
        incident.updated_at = datetime.now()
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
    recipient.updated_at = datetime.now()
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


def get_recipients_for_site(db: Session, site_id: int | None = None) -> list[Recipient]:
    """사이트의 활성 수신자 전원 (심각도 무시 — 알람 발생 시 등록자 전원 발송 정책)."""
    q = (
        db.query(Recipient)
        .outerjoin(Site, Recipient.site_id == Site.id)
        .filter(Recipient.enabled.is_(True))
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
    if "architecture" in data:
        site.architecture = data["architecture"] or None
    if "rate_limit_window_seconds" in data:
        try:
            v = int(data["rate_limit_window_seconds"])
            site.rate_limit_window_seconds = max(10, v)
        except (TypeError, ValueError):
            pass
    if "rate_limit_count" in data:
        try:
            v = int(data["rate_limit_count"])
            site.rate_limit_count = max(1, v)
        except (TypeError, ValueError):
            pass
    if "rate_limit_disabled" in data:
        site.rate_limit_disabled = bool(data["rate_limit_disabled"])
    # 알람 임계값 (서버별)
    if "alarm_enabled" in data:
        site.alarm_enabled = bool(data["alarm_enabled"])
    for f in ("cpu_threshold", "mem_threshold", "disk_threshold"):
        if f in data:
            raw = data[f]
            if raw in (None, "", "null"):
                setattr(site, f, None)  # 미감시
            else:
                try:
                    setattr(site, f, max(1, min(100, int(raw))))
                except (TypeError, ValueError):
                    pass
    if "alarm_for_seconds" in data:
        try:
            site.alarm_for_seconds = max(30, int(data["alarm_for_seconds"]))
        except (TypeError, ValueError):
            pass
    if "enabled" in data:
        site.enabled = bool(data["enabled"])
    site.updated_at = datetime.now()
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
    user.updated_at = datetime.now()
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
        user.last_login_at = datetime.now()
        db.commit()


# ── Password reset tokens ──────────────────────────────────────────────────

def create_password_reset_token(db: Session, user_id: int, token: str, expires_at: datetime) -> PasswordResetToken:
    rec = PasswordResetToken(token=token, user_id=user_id, expires_at=expires_at)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def get_password_reset_token(db: Session, token: str) -> PasswordResetToken | None:
    return db.query(PasswordResetToken).filter(PasswordResetToken.token == token).first()


def consume_password_reset_token(db: Session, token: str) -> None:
    rec = get_password_reset_token(db, token)
    if rec:
        rec.used_at = datetime.now()
        db.commit()


def recent_reset_for_user(db: Session, user_id: int, within_seconds: int = 60) -> PasswordResetToken | None:
    """rate limit 용 — within_seconds 안에 발급된 미사용 토큰이 있는지."""
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(seconds=within_seconds)
    return (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.user_id == user_id)
        .filter(PasswordResetToken.created_at >= cutoff)
        .filter(PasswordResetToken.used_at.is_(None))
        .first()
    )


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


def _notifications_query(
    db: Session,
    channel: str | None = None,
    status: str | None = None,
    search: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    q = db.query(NotificationLog)
    if channel:
        q = q.filter(NotificationLog.channel == channel)
    if status:
        q = q.filter(NotificationLog.status == status)
    if search:
        like = f"%{search.lower()}%"
        q = q.filter(
            (func.lower(NotificationLog.recipient_label).like(like))
            | (func.lower(NotificationLog.error_message).like(like))
        )
    if date_from:
        q = q.filter(NotificationLog.sent_at >= date_from)
    if date_to:
        q = q.filter(NotificationLog.sent_at <= date_to)
    return q


def list_notifications(
    db: Session,
    skip: int = 0,
    limit: int = 50,
    channel: str | None = None,
    status: str | None = None,
    search: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[NotificationLog]:
    return (
        _notifications_query(db, channel, status, search, date_from, date_to)
        .order_by(NotificationLog.sent_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def count_notifications(
    db: Session,
    channel: str | None = None,
    status: str | None = None,
    search: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> int:
    return _notifications_query(db, channel, status, search, date_from, date_to).count()


def delete_notification(db: Session, notif_id: int) -> bool:
    """알림 로그 1건 삭제. (관리자 전용 — 라우터에서 require_admin)"""
    log = db.query(NotificationLog).filter(NotificationLog.id == notif_id).first()
    if not log:
        return False
    db.delete(log)
    db.commit()
    return True


def delete_notifications_filtered(
    db: Session,
    channel: str | None = None,
    status: str | None = None,
    search: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> int:
    """현재 필터에 매칭되는 알림 로그를 일괄 삭제하고 삭제 건수 반환.
    필터가 모두 비면 전체 삭제. (관리자 전용)"""
    n = _notifications_query(db, channel, status, search, date_from, date_to).delete(
        synchronize_session=False
    )
    db.commit()
    return n
