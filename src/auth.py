"""인증 / 권한 모듈 — bcrypt 해싱, Starlette SessionMiddleware 기반 서명 쿠키."""
import logging

from fastapi import Depends, HTTPException, Request, status
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from src.config import settings
from src.db.crud import get_user, get_user_by_email, create_user, touch_user_login
from src.db.database import get_db
from src.db.models import User

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def ensure_initial_admin(db: Session) -> None:
    """서버 첫 기동 시 .env의 ADMIN_EMAIL/ADMIN_PASSWORD로 admin 자동 생성."""
    if not settings.admin_email or not settings.admin_password:
        logger.info("초기 admin 계정 환경변수 미설정 — 자동 생성 생략")
        return
    if len(settings.admin_password) < settings.min_password_length:
        logger.warning(
            "ADMIN_PASSWORD 길이가 %d자 미만 — 초기 admin 생성은 진행하나 즉시 변경 권장",
            settings.min_password_length,
        )
    existing = get_user_by_email(db, settings.admin_email)
    if existing:
        return
    create_user(
        db,
        email=settings.admin_email,
        password_hash=hash_password(settings.admin_password),
        role="admin",
        name="Admin",
    )
    logger.info("초기 admin 계정 생성: %s", settings.admin_email)


def check_security_config() -> None:
    """기동 시 약한 SESSION_SECRET / 기본 admin 비번 등 위험 설정을 경고."""
    warnings = []
    insecure_secrets = {"", "dev-insecure-change-me", "change-me-to-a-long-random-string"}
    if settings.session_secret in insecure_secrets or len(settings.session_secret) < 32:
        warnings.append(
            "SESSION_SECRET이 기본값/약한 값입니다 → "
            "`python -c \"import secrets; print(secrets.token_hex(32))\"` 결과로 교체하세요"
        )
    if settings.admin_password in ("", "changeme", "admin", "password"):
        warnings.append(
            "ADMIN_PASSWORD가 기본값/약한 값입니다 → 첫 로그인 후 즉시 변경하세요"
        )
    if not settings.session_cookie_secure:
        warnings.append(
            "SESSION_COOKIE_SECURE=false (HTTPS 환경에선 true로 켜세요)"
        )
    for w in warnings:
        logger.warning("[보안 경고] %s", w)


def login_user(request: Request, user: User) -> None:
    request.session["user_id"] = user.id


def logout_user(request: Request) -> None:
    request.session.clear()


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """현재 세션의 사용자를 반환 (없으면 None). 로그인 페이지 등에서 사용."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = get_user(db, user_id)
    if not user or not user.enabled:
        return None
    return user


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    """인증 필수 — 미인증이면 /login으로 리다이렉트할 수 있도록 401 발생."""
    user = current_user(request, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"Location": "/login"},
        )
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = get_user_by_email(db, email)
    if not user or not user.enabled:
        return None
    if not verify_password(password, user.password_hash):
        return None
    touch_user_login(db, user.id)
    return user
