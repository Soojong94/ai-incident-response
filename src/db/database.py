from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from src.config import settings


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from src.db import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _migrate()


def _migrate():
    """기존 DB에 신규 컬럼 추가 (없을 경우에만)."""
    if "sqlite" not in settings.database_url:
        return
    from sqlalchemy import text
    with engine.connect() as conn:
        # incidents 컬럼
        inc_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(incidents)"))}
        for col, ddl in [
            ("site_id", "ALTER TABLE incidents ADD COLUMN site_id INTEGER REFERENCES sites(id)"),
            ("cluster_id", "ALTER TABLE incidents ADD COLUMN cluster_id VARCHAR(36)"),
            ("ack_token", "ALTER TABLE incidents ADD COLUMN ack_token VARCHAR(40)"),
            ("acknowledged_at", "ALTER TABLE incidents ADD COLUMN acknowledged_at DATETIME"),
            ("acknowledged_by", "ALTER TABLE incidents ADD COLUMN acknowledged_by VARCHAR(200)"),
            ("escalation_level", "ALTER TABLE incidents ADD COLUMN escalation_level INTEGER DEFAULT 0"),
            ("last_escalated_at", "ALTER TABLE incidents ADD COLUMN last_escalated_at DATETIME"),
        ]:
            if col not in inc_cols:
                conn.execute(text(ddl))

        # recipients 컬럼
        recipients_info = list(conn.execute(text("PRAGMA table_info(recipients)")))
        if recipients_info:
            rec_cols = {row[1] for row in recipients_info}
            if "site_id" not in rec_cols:
                conn.execute(text("ALTER TABLE recipients ADD COLUMN site_id INTEGER REFERENCES sites(id)"))
            if "escalation_level" not in rec_cols:
                conn.execute(text("ALTER TABLE recipients ADD COLUMN escalation_level INTEGER DEFAULT 0"))

        # sites 컬럼
        sites_info = list(conn.execute(text("PRAGMA table_info(sites)")))
        if sites_info:
            site_cols = {row[1] for row in sites_info}
            for col, ddl in [
                ("resource_pattern", "ALTER TABLE sites ADD COLUMN resource_pattern VARCHAR(200)"),
                ("group_name", "ALTER TABLE sites ADD COLUMN group_name VARCHAR(100)"),
                ("alarm_enabled", "ALTER TABLE sites ADD COLUMN alarm_enabled BOOLEAN DEFAULT 1"),
                ("cpu_threshold", "ALTER TABLE sites ADD COLUMN cpu_threshold INTEGER DEFAULT 85"),
                ("mem_threshold", "ALTER TABLE sites ADD COLUMN mem_threshold INTEGER DEFAULT 90"),
                ("disk_threshold", "ALTER TABLE sites ADD COLUMN disk_threshold INTEGER DEFAULT 85"),
                ("alarm_for_seconds", "ALTER TABLE sites ADD COLUMN alarm_for_seconds INTEGER DEFAULT 300"),
                ("architecture", "ALTER TABLE sites ADD COLUMN architecture TEXT"),
                ("auto_created", "ALTER TABLE sites ADD COLUMN auto_created BOOLEAN DEFAULT 0"),
                ("rate_limit_window_seconds", "ALTER TABLE sites ADD COLUMN rate_limit_window_seconds INTEGER DEFAULT 300"),
                ("rate_limit_count", "ALTER TABLE sites ADD COLUMN rate_limit_count INTEGER DEFAULT 3"),
                ("rate_limit_disabled", "ALTER TABLE sites ADD COLUMN rate_limit_disabled BOOLEAN DEFAULT 0"),
                ("escalation_enabled", "ALTER TABLE sites ADD COLUMN escalation_enabled BOOLEAN DEFAULT 0"),
                ("escalation_delay_minutes", "ALTER TABLE sites ADD COLUMN escalation_delay_minutes INTEGER DEFAULT 10"),
            ]:
                if col not in site_cols:
                    conn.execute(text(ddl))

        conn.commit()
