from sqlalchemy import Boolean, Column, Integer, String, Text, DateTime, JSON, ForeignKey
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
    obs_bucket = Column(String(200))
    obs_object_key = Column(String(500))
    site_id = Column(Integer, ForeignKey("sites.id"), nullable=True, index=True)
    raw_alarm = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    logs = relationship("IncidentLog", back_populates="incident", cascade="all, delete-orphan")
    analysis_result = relationship("AnalysisResult", back_populates="incident", uselist=False, cascade="all, delete-orphan")
    notifications = relationship("NotificationLog", cascade="all, delete-orphan", order_by="NotificationLog.sent_at")
    site = relationship("Site", foreign_keys=[site_id])


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


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey("incidents.id"), index=True)
    recipient_id = Column(Integer, ForeignKey("recipients.id"), nullable=True)
    recipient_label = Column(String(300))   # 발송 시점의 이름/이메일/webhook 스냅샷
    channel = Column(String(20))            # email | slack
    status = Column(String(20))             # sent | failed
    error_message = Column(Text, nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(200), unique=True, index=True)
    password_hash = Column(String(255))
    name = Column(String(100), nullable=True)
    role = Column(String(20), default="viewer")  # admin | viewer
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)


class Site(Base):
    __tablename__ = "sites"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True)
    description = Column(Text, nullable=True)
    resource_pattern = Column(String(200), nullable=True)  # 글로브: team1-*, web-prod-*
    obs_bucket = Column(String(200), nullable=True)         # 정확 매치
    architecture = Column(Text, nullable=True)              # 인프라 구조 (Markdown) — AI prompt에 주입
    auto_created = Column(Boolean, default=False)           # alarm 페이로드로 자동 생성됐는지
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    recipients = relationship("Recipient", back_populates="site", cascade="all, delete-orphan")


class Recipient(Base):
    __tablename__ = "recipients"

    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("sites.id"), nullable=True)
    name = Column(String(100))
    email = Column(String(200), nullable=True)
    slack_webhook = Column(String(500), nullable=True)
    receive_critical = Column(Boolean, default=True)
    receive_high = Column(Boolean, default=True)
    receive_medium = Column(Boolean, default=False)
    receive_low = Column(Boolean, default=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    site = relationship("Site", back_populates="recipients")
