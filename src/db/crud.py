import uuid
from datetime import datetime
from sqlalchemy.orm import Session
from src.db.models import Incident, IncidentLog, AnalysisResult


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


def create_incident(db: Session, alarm_data: dict) -> Incident:
    parsed = _parse_alarm(alarm_data)
    incident = Incident(**parsed, raw_alarm=alarm_data)
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return incident


def get_incident(db: Session, incident_id: int) -> Incident | None:
    return db.query(Incident).filter(Incident.id == incident_id).first()


def get_incidents(db: Session, skip: int = 0, limit: int = 100) -> list[Incident]:
    return (
        db.query(Incident)
        .order_by(Incident.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


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
