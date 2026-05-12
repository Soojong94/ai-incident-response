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
    cluster_id = Column(String(36), nullable=True, index=True)  # 같은 site + 5분 윈도우 내 incident 묶음
    raw_alarm = Column(JSON)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)

    logs = relationship("IncidentLog", back_populates="incident", cascade="all, delete-orphan")
    analysis_result = relationship("AnalysisResult", back_populates="incident", uselist=False, cascade="all, delete-orphan")
    notifications = relationship("NotificationLog", back_populates="incident", cascade="all, delete-orphan", order_by="NotificationLog.sent_at")
    site = relationship("Site", foreign_keys=[site_id])


class IncidentLog(Base):
    __tablename__ = "incident_logs"

    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey("incidents.id"))
    source = Column(String(100))   # mock | ncp_api | ssh
    log_content = Column(Text)
    log_timestamp = Column(DateTime, default=datetime.now)

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
    created_at = Column(DateTime, default=datetime.now)

    incident = relationship("Incident", back_populates="analysis_result")


class SiteNote(Base):
    __tablename__ = "site_notes"

    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("sites.id"), index=True)
    author = Column(String(20))  # 'ai' | 'user'
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    content = Column(Text)
    pinned = Column(Boolean, default=False)  # True면 항상 AI prompt에 포함
    related_incident_id = Column(Integer, ForeignKey("incidents.id"), nullable=True)
    occurrences = Column(Integer, default=1)  # 비슷한 내용 누적 시 증가
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)


class AnalysisFeedback(Base):
    __tablename__ = "analysis_feedback"

    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey("incidents.id"), unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    rating = Column(String(10))  # 'up' | 'down'
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(64), unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    expires_at = Column(DateTime)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey("incidents.id"), index=True)
    recipient_id = Column(Integer, ForeignKey("recipients.id"), nullable=True)
    recipient_label = Column(String(300))   # 발송 시점의 이름/이메일/webhook 스냅샷
    channel = Column(String(20))            # email | slack
    status = Column(String(20))             # sent | failed
    error_message = Column(Text, nullable=True)
    sent_at = Column(DateTime, default=datetime.now)

    incident = relationship("Incident", back_populates="notifications")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(200), unique=True, index=True)
    password_hash = Column(String(255))
    name = Column(String(100), nullable=True)
    role = Column(String(20), default="viewer")  # admin | viewer
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    last_login_at = Column(DateTime, nullable=True)


class Site(Base):
    __tablename__ = "sites"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True)
    description = Column(Text, nullable=True)
    resource_pattern = Column(String(200), nullable=True)  # 글로브: team1-*, web-prod-*
    obs_bucket = Column(String(200), nullable=True)         # 정확 매치
    architecture = Column(Text, nullable=True)              # 인프라 구조 (Markdown) — AI prompt에 주입
    # API keys — 모두 Fernet 암호화 저장. 빈 값이면 .env fallback.
    ncp_access_key_enc = Column(Text, nullable=True)
    ncp_secret_key_enc = Column(Text, nullable=True)
    wms_scenario_id = Column(String(50), nullable=True)     # 평문 — 식별자만, 민감 X
    # 분석 rate-limit: 윈도우 안 incident가 count 초과 시 후속은 분석/알림 skip
    rate_limit_window_seconds = Column(Integer, default=300)  # 5분
    rate_limit_count = Column(Integer, default=3)
    rate_limit_disabled = Column(Boolean, default=False)     # 비상 모드 ON: limit 무시
    auto_created = Column(Boolean, default=False)           # alarm 페이로드로 자동 생성됐는지
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)

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
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)

    site = relationship("Site", back_populates="recipients")
