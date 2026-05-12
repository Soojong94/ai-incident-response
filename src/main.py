import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from src.config import settings
from src.db.database import init_db, get_db, SessionLocal
from src.db.crud import (
    create_incident, get_incident, get_incidents, count_incidents,
    delete_incident, delete_all_incidents, distinct_metric_types,
    list_recipients, get_recipient, create_recipient, update_recipient, delete_recipient,
    list_sites, get_site, create_site, update_site, delete_site, list_recipients_for_site,
    get_user_by_email, get_user, list_users, create_user as crud_create_user,
    update_user as crud_update_user, delete_user as crud_delete_user,
    create_password_reset_token, get_password_reset_token, consume_password_reset_token,
    recent_reset_for_user,
    list_site_notes, add_site_note, update_site_note, delete_site_note,
    get_feedback_for_incident, upsert_feedback,
    update_site_keys, clear_site_keys,
    daily_incident_counts, severity_distribution, status_distribution,
    top_sites_by_incident, notification_success_rate, avg_analysis_duration_seconds,
    list_notifications, count_notifications,
)
from src.crypto import decrypt as decrypt_secret, mask as mask_secret
from src.auth import (
    ensure_initial_admin, authenticate, login_user, logout_user,
    current_user, require_user, require_admin, hash_password, verify_password,
    check_security_config,
)
from src.webhook.handler import receive_alarm, run_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("DB initialized")
    check_security_config()
    # 초기 admin 계정 보장
    db = SessionLocal()
    try:
        ensure_initial_admin(db)
    finally:
        db.close()
    from src.collector.ncp_poller import poller
    await poller.start()
    yield
    poller.stop()


app = FastAPI(title="AI Incident Response", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="src/ui/static"), name="static")
templates = Jinja2Templates(directory="src/ui/templates")


# ── 모든 템플릿에 current_user 주입 (네비바 등 사용) ──────────────────────────

def _inject_user(request: Request, ctx: dict) -> dict:
    db = SessionLocal()
    try:
        ctx.setdefault("current_user", current_user(request, db))
    finally:
        db.close()
    return ctx


# ── Auth middleware ─────────────────────────────────────────────────────────

AUTH_EXEMPT_PATHS = {"/login", "/logout", "/favicon.svg", "/favicon.ico", "/forgot-password"}
# webhook은 외부 시스템(NCP CF)이 호출하므로 인증 면제.
# /test/는 더 이상 면제하지 않음 — 인증된 사용자만 트리거 가능.
# /reset/ 은 토큰이 자격증명 역할이라 인증 면제 prefix.
AUTH_EXEMPT_PREFIXES = ("/webhook/", "/reset/", "/static/")


def _safe_next(next_url: str) -> str:
    """open redirect 방어 — 외부 URL이나 비정상 경로면 / 로 fallback."""
    if not next_url:
        return "/"
    # 절대 URL, scheme-relative URL, backslash 우회 차단
    if next_url.startswith(("http://", "https://", "//", "\\")):
        return "/"
    if not next_url.startswith("/"):
        return "/"
    return next_url


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    from fastapi.responses import JSONResponse
    path = request.url.path
    if path in AUTH_EXEMPT_PATHS or any(path.startswith(p) for p in AUTH_EXEMPT_PREFIXES):
        return await call_next(request)
    user_id = request.session.get("user_id") if hasattr(request, "session") else None
    authed = False
    if user_id:
        db = SessionLocal()
        try:
            u = get_user(db, user_id)
            if u and u.enabled:
                authed = True
        finally:
            db.close()
    if not authed:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        # 로그인 후엔 무조건 장애 목록(/)로 진입 — next 추적 안 함
        return RedirectResponse(url="/login", status_code=303)
    return await call_next(request)


# ── 보안 응답 헤더 ──────────────────────────────────────────────────────────

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    # 운영 HTTPS 환경에서만 의미가 있는 헤더 — 쿠키 secure 모드일 때만 부여
    if settings.session_cookie_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


# SessionMiddleware는 auth_middleware/security_headers 뒤에 등록 — outer가 되어 먼저 실행되어야
# request.session이 채워진 상태로 auth_middleware로 진입.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="ai_incident_session",
    https_only=settings.session_cookie_secure,
    same_site="lax",
    max_age=settings.session_max_age_seconds,
)


# ── Login / Logout ──────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/login")
async def login_action(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = authenticate(db, email, password)
    if not user:
        return RedirectResponse(url="/login?error=invalid", status_code=303)
    login_user(request, user)
    return RedirectResponse(url="/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse(url="/login", status_code=303)


# ── 비밀번호 찾기 / 재설정 / 본인 변경 ────────────────────────────────────────

import secrets as _secrets
from datetime import timedelta


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request, sent: bool = False):
    return templates.TemplateResponse(request, "forgot_password.html", {"sent": sent})


@app.post("/forgot-password")
async def forgot_password_action(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    """이메일 입력 받아 reset link 발송. 계정 존재 여부는 응답에 노출하지 않음."""
    from src.notifier.email_notifier import send_password_reset

    email = email.strip().lower()
    user = get_user_by_email(db, email)
    if user and user.enabled:
        # rate limit — 1분 안에 발급된 미사용 토큰 있으면 재발급 안 함
        existing = recent_reset_for_user(db, user.id, within_seconds=60)
        if not existing:
            token = _secrets.token_urlsafe(32)
            expires_at = datetime.now() + timedelta(hours=1)
            create_password_reset_token(db, user.id, token, expires_at)
            base = str(request.base_url).rstrip("/")
            reset_url = f"{base}/reset/{token}"
            send_password_reset(user.email, reset_url)
        else:
            logger.info("forgot-password rate-limited for user_id=%d", user.id)
    else:
        logger.info("forgot-password: unknown or disabled email '%s'", email)
    # 보안: 항상 같은 응답
    return RedirectResponse(url="/forgot-password?sent=1", status_code=303)


@app.get("/reset/{token}", response_class=HTMLResponse)
def reset_password_page(token: str, request: Request, db: Session = Depends(get_db)):
    rec = get_password_reset_token(db, token)
    valid = bool(rec and rec.used_at is None and rec.expires_at > datetime.now())
    return templates.TemplateResponse(request, "reset_password.html", {"token": token, "valid": valid})


@app.post("/reset/{token}")
async def reset_password_action(
    token: str,
    request: Request,
    new_password: str = Form(...),
    db: Session = Depends(get_db),
):
    rec = get_password_reset_token(db, token)
    if not rec or rec.used_at is not None or rec.expires_at <= datetime.now():
        return RedirectResponse(url=f"/reset/{token}", status_code=303)
    if len(new_password) < settings.min_password_length:
        # 보안 — 사용자에게 사유 알려주려면 query 파라미터로
        return RedirectResponse(url=f"/reset/{token}?error=too_short", status_code=303)
    crud_update_user(db, rec.user_id, {"password_hash": hash_password(new_password)})
    consume_password_reset_token(db, token)
    return RedirectResponse(url="/login?reset=ok", status_code=303)


@app.get("/me/password", response_class=HTMLResponse)
def change_password_page(request: Request, user=Depends(require_user)):
    return templates.TemplateResponse(request, "change_password.html", {"current_user": user})


@app.post("/me/password")
async def change_password_action(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    if not verify_password(current_password, user.password_hash):
        return RedirectResponse(url="/me/password?error=wrong_current", status_code=303)
    if len(new_password) < settings.min_password_length:
        return RedirectResponse(url="/me/password?error=too_short", status_code=303)
    crud_update_user(db, user.id, {"password_hash": hash_password(new_password)})
    return RedirectResponse(url="/me/password?ok=1", status_code=303)


# ── User management (admin) ─────────────────────────────────────────────────

def _user_to_dict(u) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "role": u.role,
        "enabled": u.enabled,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


@app.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    users = list_users(db)
    return templates.TemplateResponse(request, "users.html", {"users": users, "current_user": admin})


@app.get("/api/users")
def api_list_users(db: Session = Depends(get_db), _admin=Depends(require_admin)) -> list[dict]:
    return [_user_to_dict(u) for u in list_users(db)]


def _validate_password(password: str) -> None:
    if len(password) < settings.min_password_length:
        raise HTTPException(
            status_code=400,
            detail=f"비밀번호는 최소 {settings.min_password_length}자 이상이어야 합니다",
        )


@app.post("/api/users", status_code=201)
async def api_create_user(request: Request, db: Session = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    data = await request.json()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email:
        raise HTTPException(status_code=400, detail="이메일은 필수입니다")
    _validate_password(password)
    if get_user_by_email(db, email):
        raise HTTPException(status_code=409, detail="이미 사용 중인 이메일입니다")
    user = crud_create_user(
        db,
        email=email,
        password_hash=hash_password(password),
        role=data.get("role", "viewer"),
        name=data.get("name") or None,
    )
    return _user_to_dict(user)


@app.put("/api/users/{user_id}")
async def api_update_user(user_id: int, request: Request, db: Session = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    data = await request.json()
    update = {
        k: v for k, v in data.items()
        if k in ("name", "role", "enabled")
    }
    if data.get("password"):
        _validate_password(data["password"])
        update["password_hash"] = hash_password(data["password"])
    user = crud_update_user(db, user_id, update)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_to_dict(user)


@app.delete("/api/users/{user_id}")
def api_delete_user(user_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)) -> dict:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="자기 계정은 삭제할 수 없습니다")
    if not crud_delete_user(db, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    return {"deleted": user_id}


# ── Favicon ─────────────────────────────────────────────────────────────────

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#d69e2e"/><stop offset="1" stop-color="#c05621"/>
</linearGradient></defs>
<rect width="32" height="32" rx="7" fill="url(#g)"/>
<path d="M16 4.2 L6.5 7.2 v8.3 c0 5.5 3.6 9.2 9.5 11.3 c5.9-2.1 9.5-5.8 9.5-11.3 V7.2 z" fill="#1a202c"/>
<text x="16" y="20.3" font-family="Arial Black, system-ui, sans-serif" font-size="10.5" font-weight="900" fill="#d69e2e" text-anchor="middle" letter-spacing="-0.5">AI</text>
</svg>"""


@app.get("/favicon.svg", include_in_schema=False)
def favicon_svg():
    return Response(content=FAVICON_SVG, media_type="image/svg+xml")


@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico():
    # SVG로 동일하게 응답 — 일부 브라우저가 .ico 경로로 자동 요청함
    return Response(content=FAVICON_SVG, media_type="image/svg+xml")


# ── Webhook ──────────────────────────────────────────────────────────────────

@app.post("/webhook/alarm", status_code=202)
async def webhook_alarm(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    payload = await request.json()
    result = await receive_alarm(request, payload, db)
    incident_id = result["incident_id"]
    background_tasks.add_task(run_pipeline, incident_id, payload)
    return {"status": "accepted", "incident_id": incident_id}


# ── 수동 재분석 (admin) ─────────────────────────────────────────────────────

@app.post("/api/incidents/{incident_id}/reanalyze", status_code=202)
async def api_reanalyze_incident(
    incident_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
) -> dict:
    incident = get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    # 기존 logs/analysis는 그대로 두고, raw_alarm으로 파이프라인 재실행
    payload = incident.raw_alarm or {}
    # 상태 reset
    from src.db.crud import update_incident_status
    update_incident_status(db, incident_id, "processing")
    background_tasks.add_task(run_pipeline, incident_id, payload)
    return {"status": "reanalyze_started", "incident_id": incident_id}


# ── OBS 원본 로그 다운로드 ───────────────────────────────────────────────────────

@app.get("/api/incidents/{incident_id}/logs/raw")
async def api_logs_raw(incident_id: int, db: Session = Depends(get_db), _user=Depends(require_user)):
    from fastapi.responses import StreamingResponse
    from src.collector.obs_collector import _s3_client
    from src.db.crud import get_site_ncp_keys
    from botocore.exceptions import ClientError, BotoCoreError

    incident = get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    if not incident.obs_object_key or not incident.obs_bucket:
        raise HTTPException(status_code=404, detail="No OBS file for this incident")

    # site별 NCP 키가 있으면 사용, 없으면 .env fallback
    access_key, secret_key = (None, None)
    if incident.site_id:
        access_key, secret_key = get_site_ncp_keys(db, incident.site_id)

    try:
        resp = _s3_client(access_key, secret_key).get_object(
            Bucket=incident.obs_bucket, Key=incident.obs_object_key
        )
        body = resp["Body"].read()
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NoSuchBucket"):
            raise HTTPException(
                status_code=410,
                detail=f"OBS 객체가 더 이상 존재하지 않습니다 ({incident.obs_bucket}/{incident.obs_object_key}). 라이프사이클 정책에 의해 삭제됐을 수 있습니다.",
            )
        if code in ("AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"):
            raise HTTPException(
                status_code=502,
                detail=f"OBS 접근 권한 없음 ({code}) — 사이트의 NCP 키 또는 .env 키를 확인하세요.",
            )
        logger.exception("OBS get_object 실패 (incident=%d)", incident_id)
        raise HTTPException(status_code=502, detail=f"OBS 오류: {code or 'unknown'}")
    except BotoCoreError as e:
        logger.exception("boto3 오류 (incident=%d)", incident_id)
        raise HTTPException(status_code=502, detail=f"OBS 통신 실패: {e}")

    filename = incident.obs_object_key.split("/")[-1]
    return StreamingResponse(
        iter([body]),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Incident 삭제 ─────────────────────────────────────────────────────────────

@app.delete("/api/incidents/{incident_id}", status_code=200)
def api_delete_incident(incident_id: int, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    if not delete_incident(db, incident_id):
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"deleted": incident_id}


@app.delete("/api/incidents", status_code=200)
def api_delete_all_incidents(db: Session = Depends(get_db), _admin=Depends(require_admin)):
    count = delete_all_incidents(db)
    return {"deleted": count}


@app.post("/api/incidents-bulk-delete", status_code=200)
async def api_bulk_delete_incidents(request: Request, db: Session = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    """{"ids": [1, 2, 3]} 형식으로 받아 일괄 삭제.
    /api/incidents/{id} path 매칭과 충돌하지 않도록 별도 경로로 분리."""
    data = await request.json()
    ids = data.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="ids 배열을 보내주세요")
    deleted = 0
    for inc_id in ids:
        try:
            if delete_incident(db, int(inc_id)):
                deleted += 1
        except (TypeError, ValueError):
            continue
    return {"deleted": deleted, "requested": len(ids)}


# ── Dashboard ────────────────────────────────────────────────────────────────

ALLOWED_PAGE_SIZES = [20, 50, 100]


def _normalize_pagination(page: int, size: int) -> tuple[int, int]:
    if size not in ALLOWED_PAGE_SIZES:
        size = 20
    page = max(1, page)
    return page, size


def _parse_date(s: str, end_of_day: bool = False) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = datetime.strptime(s, "%Y-%m-%d")
        if end_of_day:
            d = d.replace(hour=23, minute=59, second=59)
        return d
    except ValueError:
        return None


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    page: int = 1,
    size: int = 20,
    q: str = "",
    site_id: int | None = None,
    severity: str = "",
    status: str = "",
    metric_type: str = "",
    date_from: str = "",
    date_to: str = "",
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    page, size = _normalize_pagination(page, size)
    search = q.strip() or None
    sev = severity.strip() or None
    st = status.strip() or None
    mt = metric_type.strip() or None
    df = _parse_date(date_from)
    dt = _parse_date(date_to, end_of_day=True)
    total = count_incidents(db, search=search, site_id=site_id, severity=sev, status=st,
                            metric_type=mt, date_from=df, date_to=dt)
    total_pages = max(1, (total + size - 1) // size)
    if page > total_pages:
        page = total_pages
    incidents = get_incidents(db, skip=(page - 1) * size, limit=size, search=search,
                              site_id=site_id, severity=sev, status=st,
                              metric_type=mt, date_from=df, date_to=dt)
    sites = list_sites(db)
    metric_types = distinct_metric_types(db)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "incidents": incidents,
            "page": page,
            "size": size,
            "total": total,
            "total_pages": total_pages,
            "allowed_sizes": ALLOWED_PAGE_SIZES,
            "search_query": q,
            "site_id": site_id,
            "sites": sites,
            "severity_filter": severity,
            "status_filter": status,
            "metric_type_filter": metric_type,
            "metric_types": metric_types,
            "date_from_filter": date_from,
            "date_to_filter": date_to,
            "current_user": user,
        },
    )


@app.get("/incidents/{incident_id}", response_class=HTMLResponse)
def incident_detail(incident_id: int, request: Request, db: Session = Depends(get_db), user=Depends(require_user)):
    incident = get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    feedback = get_feedback_for_incident(db, incident_id)
    # 같은 resource_name 으로 최근 발생한 incident (자신 제외, 최근 10건)
    siblings = []
    if incident.resource_name:
        from src.db.models import Incident as _Inc
        siblings = (
            db.query(_Inc)
            .filter(_Inc.resource_name == incident.resource_name)
            .filter(_Inc.id != incident_id)
            .order_by(_Inc.created_at.desc())
            .limit(10)
            .all()
        )
    return templates.TemplateResponse(
        request, "detail.html",
        {"incident": incident, "feedback": feedback, "siblings": siblings, "current_user": user},
    )


# ── 운영 통계 (admin 전용) ───────────────────────────────────────────────────

@app.get("/stats", response_class=HTMLResponse)
def stats_page(
    request: Request,
    days: int = 30,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    days = max(1, min(days, 90))
    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "days": days,
            "daily": daily_incident_counts(db, min(days, 30)),
            "severity": severity_distribution(db, days),
            "status": status_distribution(db, days),
            "top_sites": top_sites_by_incident(db, days, 8),
            "notif": notification_success_rate(db, days),
            "analysis_dur": avg_analysis_duration_seconds(db, days),
            "current_user": admin,
        },
    )


# ── 알림 발송 로그 (admin 전용) ───────────────────────────────────────────────

@app.get("/notifications", response_class=HTMLResponse)
def notifications_page(
    request: Request,
    page: int = 1,
    size: int = 50,
    channel: str = "",
    status: str = "",
    q: str = "",
    date_from: str = "",
    date_to: str = "",
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    page = max(1, page)
    size = max(10, min(size, 200))
    ch = channel.strip() or None
    st = status.strip() or None
    search = q.strip() or None
    df = _parse_date(date_from)
    dt = _parse_date(date_to, end_of_day=True)
    total = count_notifications(db, channel=ch, status=st, search=search, date_from=df, date_to=dt)
    total_pages = max(1, (total + size - 1) // size)
    if page > total_pages:
        page = total_pages
    logs = list_notifications(
        db, skip=(page - 1) * size, limit=size,
        channel=ch, status=st, search=search, date_from=df, date_to=dt,
    )
    return templates.TemplateResponse(
        request, "notifications.html",
        {
            "logs": logs,
            "page": page,
            "size": size,
            "total": total,
            "total_pages": total_pages,
            "channel_filter": channel,
            "status_filter": status,
            "search_query": q,
            "date_from_filter": date_from,
            "date_to_filter": date_to,
            "current_user": admin,
        },
    )


# ── Sites + Recipients UI + API ──────────────────────────────────────────────

def _recipient_to_dict(r) -> dict:
    return {
        "id": r.id,
        "site_id": r.site_id,
        "name": r.name,
        "email": r.email,
        "slack_webhook": r.slack_webhook,
        "receive_critical": r.receive_critical,
        "receive_high": r.receive_high,
        "receive_medium": r.receive_medium,
        "receive_low": r.receive_low,
        "enabled": r.enabled,
    }


def _site_to_dict(s, include_recipients: bool = False) -> dict:
    out = {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "resource_pattern": s.resource_pattern,
        "obs_bucket": s.obs_bucket,
        "architecture": s.architecture,
        "has_architecture": bool(s.architecture),
        "auto_created": s.auto_created,
        "enabled": s.enabled,
        "wms_scenario_id": s.wms_scenario_id,
        # 키는 마스킹된 값만 노출 — 평문은 절대 응답에 안 보냄
        "ncp_access_key_masked": mask_secret(decrypt_secret(s.ncp_access_key_enc)) if s.ncp_access_key_enc else "",
        "has_ncp_keys": bool(s.ncp_access_key_enc and s.ncp_secret_key_enc),
        "recipient_count": len(s.recipients) if s.recipients is not None else 0,
    }
    if include_recipients:
        out["recipients"] = [_recipient_to_dict(r) for r in (s.recipients or [])]
    return out


@app.get("/sites", response_class=HTMLResponse)
def sites_page(request: Request, db: Session = Depends(get_db), user=Depends(require_user)):
    sites = list_sites(db)
    return templates.TemplateResponse(request, "sites.html", {"sites": sites, "current_user": user})


@app.get("/sites/{site_id}", response_class=HTMLResponse)
def site_detail_page(site_id: int, request: Request, db: Session = Depends(get_db), user=Depends(require_user)):
    site = get_site(db, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    notes = list_site_notes(db, site_id)
    recent_incidents = get_incidents(db, skip=0, limit=10, site_id=site_id)
    return templates.TemplateResponse(
        request, "site_detail.html",
        {
            "site": site,
            "notes": notes,
            "recent_incidents": recent_incidents,
            "current_user": user,
        },
    )


# ── Sites API ────────────────────────────────────────────────────────────────

@app.get("/api/sites")
def api_list_sites(db: Session = Depends(get_db)) -> list[dict]:
    return [_site_to_dict(s, include_recipients=True) for s in list_sites(db)]


@app.post("/api/sites", status_code=201)
async def api_create_site(request: Request, db: Session = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    data = await request.json()
    site = create_site(db, data)
    return _site_to_dict(site)


@app.put("/api/sites/{site_id}")
async def api_update_site(site_id: int, request: Request, db: Session = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    data = await request.json()
    site = update_site(db, site_id, data)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return _site_to_dict(site)


@app.delete("/api/sites/{site_id}")
def api_delete_site(site_id: int, db: Session = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    if not delete_site(db, site_id):
        raise HTTPException(status_code=404, detail="Site not found")
    return {"deleted": site_id}


# ── Site API keys (encrypted) ───────────────────────────────────────────────

@app.put("/api/sites/{site_id}/keys")
async def api_update_site_keys(site_id: int, request: Request, db: Session = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    """site의 NCP 키를 저장 (자동 암호화). 빈 값은 변경 안 함."""
    data = await request.json()
    access = (data.get("ncp_access_key") or "").strip() or None
    secret = (data.get("ncp_secret_key") or "").strip() or None
    if access is None and secret is None:
        raise HTTPException(status_code=400, detail="ncp_access_key 또는 ncp_secret_key 중 하나는 입력해야 합니다")
    site = update_site_keys(db, site_id, access, secret)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return _site_to_dict(site)


@app.delete("/api/sites/{site_id}/keys")
def api_clear_site_keys(site_id: int, db: Session = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    site = clear_site_keys(db, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return _site_to_dict(site)


# ── Cloud Function bundle 다운로드 ────────────────────────────────────────────

CF_BUNDLE_README_TEMPLATE = """# NCP Cloud Function 배포 가이드 — 사이트 "{site_name}"

이 ZIP은 본 사이트 전용으로 자동 생성된 Cloud Function 코드입니다.
NCP Cloud Function 콘솔에서 액션 2개 + 트리거 2개를 등록하면 됩니다.

## 사이트 식별자 (코드에 이미 채워져 있음)
- WMS scenario ID: {wms_scenario_id}
- OBS 버킷: {obs_bucket}
- AI 서버 webhook: {webhook_url}
- 기본 resource_name: {resource_name}

## 1. 액션 등록

### 액션 1 — cf_wms_poll
- 런타임: python:3.13
- 진입점: `main`
- 코드: `cf_wms_poll/main.py` 내용 전체 붙여넣기
- **디폴트 파라미터** (반드시 입력):
```json
{{
  "access_key": "<NCP IAM Access Key>",
  "secret_key": "<NCP IAM Secret Key>"
}}
```
> ⚠️ 보안: 본 시스템은 사이트에 저장된 NCP 키를 ZIP에 포함하지 않습니다.
> NCP CF 콘솔의 "디폴트 파라미터"에 직접 입력하세요.

### 액션 2 — cf_obs_to_webhook
- 런타임: python:3.13
- 진입점: `main`
- 코드: `cf_obs_to_webhook/main.py` 내용 전체 붙여넣기
- 디폴트 파라미터: 비움 (OBS 이벤트가 자동 전달)

## 2. 트리거 등록

### 트리거 1 — Cron (5분 주기)
- 타입: Cron
- 표현식: `*/5 * * * *`
- 타임존: UTC +09:00 (KST)
- 연결 액션: `cf_wms_poll`

### 트리거 2 — Object Storage Event
- 타입: Object Storage Event
- 버킷: `{obs_bucket}`
- 이벤트: Object Created
- 연결 액션: `cf_obs_to_webhook`

## 3. 동작 확인
1. 모니터링 대상에서 의도적으로 에러 발생 (예: nginx 500)
2. 5분 내 cron 발화 → WMS errorCount ≥ 1 확인 → CLA export
3. OBS 새 파일 도착 → CF#1 발화 → AI 서버에 webhook
4. AI 분석 완료 후 등록된 수신자에게 이메일/Slack 발송

## 4. 트러블슈팅
- CF 액션 로그에서 `status: 200` + `body` 확인
- WMS 시나리오 ID가 잘못되면 빈 결과
- OBS 버킷이 없거나 권한 부족이면 export 실패
"""


def _render_cf_files(site) -> dict:
    """site의 식별자를 cloud-functions/*.py에 치환해 ZIP에 넣을 (path → bytes) 매핑 반환."""
    import re
    base_dir = "cloud-functions"

    with open(f"{base_dir}/cf_wms_poll/main.py", "r", encoding="utf-8") as f:
        wms_src = f.read()
    # SCENARIO_ID = ... 줄 치환
    scenario = site.wms_scenario_id or "0"
    bucket = site.obs_bucket or "your-bucket"
    wms_src = re.sub(r"^SCENARIO_ID\s*=.*$", f"SCENARIO_ID = {scenario}", wms_src, count=1, flags=re.M)
    wms_src = re.sub(r'^OBS_BUCKET\s*=.*$', f'OBS_BUCKET = "{bucket}"', wms_src, count=1, flags=re.M)

    with open(f"{base_dir}/cf_obs_to_webhook/main.py", "r", encoding="utf-8") as f:
        obs_src = f.read()
    # RESOURCE_NAME 줄 치환 — 사이트 이름 또는 패턴 fallback
    resource_name = site.resource_pattern or site.name or "alarmed-host"
    # 와일드카드 별표는 CF에서 그대로 두지 말고 placeholder
    if "*" in resource_name:
        resource_name = resource_name.replace("*", "host")
    obs_src = re.sub(r'^RESOURCE_NAME\s*=.*$', f'RESOURCE_NAME = "{resource_name}"', obs_src, count=1, flags=re.M)

    webhook_url = f"{settings.public_base_url.rstrip('/')}/webhook/alarm"
    obs_src = re.sub(r'^WEBHOOK_URL\s*=.*$', f'WEBHOOK_URL = "{webhook_url}"', obs_src, count=1, flags=re.M)
    readme = CF_BUNDLE_README_TEMPLATE.format(
        site_name=site.name,
        wms_scenario_id=site.wms_scenario_id or "(미등록 — site 수정에서 입력)",
        obs_bucket=site.obs_bucket or "(미등록)",
        webhook_url=webhook_url,
        resource_name=resource_name,
    )

    return {
        "cf_wms_poll/main.py": wms_src.encode("utf-8"),
        "cf_obs_to_webhook/main.py": obs_src.encode("utf-8"),
        "README.md": readme.encode("utf-8"),
    }


# ── CF 등록 가이드 (admin) ──────────────────────────────────────────────────

@app.get("/guide", response_class=HTMLResponse)
def guide_index(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """사이트 목록 — 각 사이트의 CF 등록 가이드 진입점."""
    sites = list_sites(db)
    return templates.TemplateResponse(
        request, "guide_index.html",
        {"sites": sites, "current_user": admin},
    )


@app.get("/sites/{site_id}/guide", response_class=HTMLResponse)
def site_guide(site_id: int, request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """site별 NCP Cloud Function 등록 가이드 (코드 + 절차 + 디폴트 파라미터 안내)."""
    site = get_site(db, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    files = _render_cf_files(site)
    wms_code = files["cf_wms_poll/main.py"].decode("utf-8")
    obs_code = files["cf_obs_to_webhook/main.py"].decode("utf-8")
    webhook_url = f"{settings.public_base_url.rstrip('/')}/webhook/alarm"
    return templates.TemplateResponse(
        request, "cf_guide.html",
        {
            "site": site,
            "wms_code": wms_code,
            "obs_code": obs_code,
            "webhook_url": webhook_url,
            "current_user": admin,
        },
    )


# ── Recipients API (사이트 종속) ─────────────────────────────────────────────

@app.get("/api/sites/{site_id}/recipients")
def api_list_recipients_for_site(site_id: int, db: Session = Depends(get_db)) -> list[dict]:
    return [_recipient_to_dict(r) for r in list_recipients_for_site(db, site_id)]


@app.post("/api/sites/{site_id}/recipients", status_code=201)
async def api_create_recipient_in_site(site_id: int, request: Request, db: Session = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    if not get_site(db, site_id):
        raise HTTPException(status_code=404, detail="Site not found")
    data = await request.json()
    data["site_id"] = site_id
    recipient = create_recipient(db, data)
    return _recipient_to_dict(recipient)


@app.put("/api/recipients/{recipient_id}")
async def api_update_recipient(recipient_id: int, request: Request, db: Session = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    data = await request.json()
    recipient = update_recipient(db, recipient_id, data)
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")
    return _recipient_to_dict(recipient)


@app.delete("/api/recipients/{recipient_id}")
def api_delete_recipient(recipient_id: int, db: Session = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    if not delete_recipient(db, recipient_id):
        raise HTTPException(status_code=404, detail="Recipient not found")
    return {"deleted": recipient_id}


# ── Site notes API ──────────────────────────────────────────────────────────

def _note_to_dict(n) -> dict:
    return {
        "id": n.id,
        "site_id": n.site_id,
        "author": n.author,
        "user_id": n.user_id,
        "content": n.content,
        "pinned": n.pinned,
        "occurrences": n.occurrences,
        "related_incident_id": n.related_incident_id,
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "updated_at": n.updated_at.isoformat() if n.updated_at else None,
    }


@app.get("/api/sites/{site_id}/notes")
def api_list_site_notes(site_id: int, db: Session = Depends(get_db), _u=Depends(require_user)) -> list[dict]:
    return [_note_to_dict(n) for n in list_site_notes(db, site_id)]


@app.post("/api/sites/{site_id}/notes", status_code=201)
async def api_add_site_note(site_id: int, request: Request, db: Session = Depends(get_db), user=Depends(require_admin)) -> dict:
    if not get_site(db, site_id):
        raise HTTPException(status_code=404, detail="Site not found")
    data = await request.json()
    content = (data.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content는 필수입니다")
    note = add_site_note(db, site_id, author="user", content=content, user_id=user.id)
    return _note_to_dict(note)


@app.put("/api/site-notes/{note_id}")
async def api_update_site_note(note_id: int, request: Request, db: Session = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    data = await request.json()
    note = update_site_note(db, note_id, data)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return _note_to_dict(note)


@app.delete("/api/site-notes/{note_id}")
def api_delete_site_note(note_id: int, db: Session = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    if not delete_site_note(db, note_id):
        raise HTTPException(status_code=404, detail="Note not found")
    return {"deleted": note_id}


# ── Analysis feedback API ───────────────────────────────────────────────────

@app.get("/api/incidents/{incident_id}/feedback")
def api_get_feedback(incident_id: int, db: Session = Depends(get_db), _u=Depends(require_user)) -> dict:
    fb = get_feedback_for_incident(db, incident_id)
    if not fb:
        return {"rating": None, "comment": None}
    return {"rating": fb.rating, "comment": fb.comment, "user_id": fb.user_id}


@app.post("/api/incidents/{incident_id}/feedback")
async def api_set_feedback(incident_id: int, request: Request, db: Session = Depends(get_db), user=Depends(require_user)) -> dict:
    data = await request.json()
    rating = data.get("rating")
    if rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating은 'up' 또는 'down'")
    comment = (data.get("comment") or "").strip() or None
    fb = upsert_feedback(db, incident_id, user.id, rating, comment)

    # 👍 받은 분석은 그 incident의 site의 메모로 자동 승격 (해당 incident.cause_category 기반)
    if rating == "up":
        inc = get_incident(db, incident_id)
        if inc and inc.site_id and inc.analysis_result:
            cause = (inc.analysis_result.cause_category or "").strip()
            detail = (inc.analysis_result.cause_detail or "").strip()
            if cause and detail:
                header = f"[{cause}] "
                content = f"✓ 검증됨 — {header}{detail.split('. ')[0][:200]}"
                if comment:
                    content += f" / 메모: {comment[:120]}"
                note = add_site_note(
                    db, inc.site_id, author="user", content=content,
                    user_id=user.id, related_incident_id=incident_id,
                )
                # 검증된 메모는 즉시 pin
                update_site_note(db, note.id, {"pinned": True})
    return {"rating": fb.rating, "comment": fb.comment}


@app.post("/api/recipients/{recipient_id}/test")
def api_test_recipient(recipient_id: int, db: Session = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    """수신자 이메일로 즉시 샘플 알림을 보낸다 — SMTP 설정/주소 검증용."""
    from src.notifier.email_notifier import send_analysis_complete

    r = get_recipient(db, recipient_id)
    if not r:
        raise HTTPException(status_code=404, detail="Recipient not found")
    if not r.email:
        raise HTTPException(status_code=400, detail="이 수신자는 이메일이 등록돼 있지 않습니다")
    sample = {
        "cause_category": "테스트",
        "cause_detail": "이 메시지는 수신자 설정이 정상인지 확인하기 위한 샘플 알림입니다. 실제 장애가 발생한 것은 아닙니다.",
        "severity": "Medium",
        "impact_scope": "테스트 — 실제 영향 없음",
        "immediate_actions": [
            "이 메일이 정상 수신됐는지 확인",
            "수신함/스팸 분류 점검",
            "필요 시 수신자 정보 수정",
        ],
        "prevention": "수신자 등록 후에는 가끔 테스트로 발송 확인을 권장합니다.",
        "confidence": "높음",
    }
    ok, err = send_analysis_complete(0, "[테스트] 수신자 설정 확인", sample, recipient_email=r.email)
    return {"ok": ok, "email": r.email, "error": err}


# ── API (JSON) ────────────────────────────────────────────────────────────────

@app.get("/api/incidents")
def api_incidents(
    page: int = 1,
    size: int = 20,
    q: str = "",
    site_id: int | None = None,
    severity: str = "",
    status: str = "",
    metric_type: str = "",
    date_from: str = "",
    date_to: str = "",
    db: Session = Depends(get_db),
) -> dict:
    page, size = _normalize_pagination(page, size)
    search = q.strip() or None
    sev = severity.strip() or None
    st = status.strip() or None
    mt = metric_type.strip() or None
    df = _parse_date(date_from)
    dt = _parse_date(date_to, end_of_day=True)
    total = count_incidents(db, search=search, site_id=site_id, severity=sev, status=st,
                            metric_type=mt, date_from=df, date_to=dt)
    total_pages = max(1, (total + size - 1) // size)
    if page > total_pages:
        page = total_pages
    incidents = get_incidents(db, skip=(page - 1) * size, limit=size, search=search,
                              site_id=site_id, severity=sev, status=st,
                              metric_type=mt, date_from=df, date_to=dt)
    items = [
        {
            "id": inc.id,
            "alarm_name": inc.alarm_name,
            "resource_name": inc.resource_name,
            "metric_type": inc.metric_type,
            "current_value": inc.current_value,
            "alarm_time": inc.alarm_time.isoformat() if inc.alarm_time else None,
            "status": inc.status,
            "severity": inc.severity,
            "site_id": inc.site_id,
            "created_at": inc.created_at.isoformat() if inc.created_at else None,
        }
        for inc in incidents
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "total_pages": total_pages,
    }


@app.get("/api/incidents/{incident_id}")
def api_incident_detail(incident_id: int, db: Session = Depends(get_db)) -> Any:
    incident = get_incident(db, incident_id)
    if not incident:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Incident not found")
    analysis = incident.analysis_result
    return {
        "id": incident.id,
        "alarm_name": incident.alarm_name,
        "resource_name": incident.resource_name,
        "metric_type": incident.metric_type,
        "threshold_value": incident.threshold_value,
        "current_value": incident.current_value,
        "alarm_time": incident.alarm_time.isoformat() if incident.alarm_time else None,
        "status": incident.status,
        "severity": incident.severity,
        "obs_file_url": (
            f"https://kr.object.ncloudstorage.com/{incident.obs_bucket}/{incident.obs_object_key}"
            if incident.obs_object_key else None
        ),
        "logs": [{"content": l.log_content, "source": l.source} for l in incident.logs],
        "analysis": {
            "cause_category": analysis.cause_category,
            "cause_detail": analysis.cause_detail,
            "severity": analysis.severity,
            "impact_scope": analysis.impact_scope,
            "immediate_actions": analysis.immediate_actions,
            "prevention": analysis.prevention,
            "confidence": analysis.confidence,
        } if analysis else None,
    }
