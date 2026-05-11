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
            ("obs_bucket", "ALTER TABLE incidents ADD COLUMN obs_bucket VARCHAR(200)"),
            ("obs_object_key", "ALTER TABLE incidents ADD COLUMN obs_object_key VARCHAR(500)"),
            ("site_id", "ALTER TABLE incidents ADD COLUMN site_id INTEGER REFERENCES sites(id)"),
        ]:
            if col not in inc_cols:
                conn.execute(text(ddl))

        # recipients 컬럼
        recipients_info = list(conn.execute(text("PRAGMA table_info(recipients)")))
        if recipients_info:
            rec_cols = {row[1] for row in recipients_info}
            if "site_id" not in rec_cols:
                conn.execute(text("ALTER TABLE recipients ADD COLUMN site_id INTEGER REFERENCES sites(id)"))

        # sites 컬럼
        sites_info = list(conn.execute(text("PRAGMA table_info(sites)")))
        if sites_info:
            site_cols = {row[1] for row in sites_info}
            for col, ddl in [
                ("resource_pattern", "ALTER TABLE sites ADD COLUMN resource_pattern VARCHAR(200)"),
                ("obs_bucket", "ALTER TABLE sites ADD COLUMN obs_bucket VARCHAR(200)"),
                ("architecture", "ALTER TABLE sites ADD COLUMN architecture TEXT"),
                ("auto_created", "ALTER TABLE sites ADD COLUMN auto_created BOOLEAN DEFAULT 0"),
            ]:
                if col not in site_cols:
                    conn.execute(text(ddl))

        conn.commit()
