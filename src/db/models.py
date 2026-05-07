from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from src.db.database import Base


class Incident(Base):
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, index=True)
    alarm_id = Column(String(100), unique=True, index=True)
    alarm_name = Column(String(200))
    resource_name = Column(String(200))
    metric_type = Column(String(100))
    threshold_value = Column(String(50))
    current_value = Column(String(50))
    alarm_time = Column(DateTime)
    status = Column(String(50), default="processing")  # processing | analyzed | ai_failed
    severity = Column(String(50))                       # Critical | High | Medium | Low
    raw_alarm = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    logs = relationship("IncidentLog", back_populates="incident", cascade="all, delete-orphan")
    analysis_result = relationship("AnalysisResult", back_populates="incident", uselist=False, cascade="all, delete-orphan")


class IncidentLog(Base):
    __tablename__ = "incident_logs"

    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey("incidents.id"))
    source = Column(String(100))   # mock | ncp_api | ssh
    log_content = Column(Text)
    log_timestamp = Column(DateTime, default=datetime.utcnow)

    incident = relationship("Incident", back_populates="logs")


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey("incidents.id"), unique=True)
    cause_category = Column(String(100))
    cause_detail = Column(Text)
    severity = Column(String(50))
    impact_scope = Column(Text)
    immediate_actions = Column(JSON)   # list[str]
    prevention = Column(Text)
    confidence = Column(String(50))    # 높음 | 보통 | 낮음
    raw_response = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    incident = relationship("Incident", back_populates="analysis_result")
