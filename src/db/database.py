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
    with engine.connect() as conn:
        from sqlalchemy import text
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(incidents)"))}
        for col, ddl in [
            ("obs_bucket", "ALTER TABLE incidents ADD COLUMN obs_bucket VARCHAR(200)"),
            ("obs_object_key", "ALTER TABLE incidents ADD COLUMN obs_object_key VARCHAR(500)"),
        ]:
            if col not in cols:
                conn.execute(text(ddl))
        conn.commit()
