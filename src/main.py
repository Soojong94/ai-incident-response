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
    list_sites, get_site, update_site, delete_site, list_recipients_for_site,
    get_user_by_email, get_user, list_users, create_user as crud_create_user,
    update_user as crud_update_user, delete_user as crud_delete_user,
    create_password_reset_token, get_password_reset_token, consume_password_reset_token,
    recent_reset_for_user,
    list_site_notes, add_site_note, update_site_note, delete_site_note, delete_site_notes,
    get_feedback_for_incident, upsert_feedback,
    daily_incident_counts, severity_distribution, status_distribution,
    top_sites_by_incident, notification_success_rate, avg_analysis_duration_seconds,
    list_notifications, count_notifications,
    delete_notification, delete_notifications_filtered,
)
from src.auth import (
    ensure_initial_admin, authenticate, login_user, logout_user,
    current_user, require_user, require_admin, hash_password, verify_password,
    check_security_config,
)
from src.webhook.handler import run_pipeline, parse_alertmanager_payload

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
        from src.alarm_rules import write_rules
        write_rules(db)   # 사이트별 알람 임계값 → vmalert 룰 초기 생성
    finally:
        db.close()
    # 백그라운드 루프 — 서버 무응답(dead-man) 감지 + 에스컬레이션
    import asyncio
    from src.deadman import deadman_loop
    from src.escalation import escalation_loop
    deadman_task = asyncio.create_task(deadman_loop())
    escalation_task = asyncio.create_task(escalation_loop())
    yield
    deadman_task.cancel()
    escalation_task.cancel()


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
# /webhook/ 은 Alertmanager가 내부에서 호출(세션 없음) → 면제. 외부 노출은 nginx가 차단 + WEBHOOK_SECRET.
# /reset/·/ack/ 는 토큰이 자격증명 역할이라 면제 prefix.
AUTH_EXEMPT_PREFIXES = ("/webhook/", "/reset/", "/static/", "/ack/")


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
        # 로그인 후 원래 가려던 경로로 돌아가도록 next 보존 (open-redirect 방어는 _safe_next)
        import urllib.parse
        return RedirectResponse(url=f"/login?next={urllib.parse.quote(path, safe='/')}", status_code=303)
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
def login_page(request: Request, error: str = "", next: str = ""):
    return templates.TemplateResponse(request, "login.html", {"error": error, "next_url": _safe_next(next)})


@app.post("/login")
async def login_action(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    user = authenticate(db, email, password)
    if not user:
        import urllib.parse
        q = f"&next={urllib.parse.quote(next, safe='/')}" if next else ""
        return RedirectResponse(url=f"/login?error=invalid{q}", status_code=303)
    login_user(request, user)
    return RedirectResponse(url=_safe_next(next), status_code=303)


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


# ── 에이전트 설치 가이드 (에이전트 기반 온보딩) ──────────────────────────────

@app.get("/guide", response_class=HTMLResponse)
def guide_page(request: Request, user=Depends(require_user)):
    """① 설치 가이드 — 대상 서버에 Alloy 설치 → 메트릭/로그 전송."""
    return templates.TemplateResponse(request, "guide.html", {"current_user": user})


@app.get("/guide/alerts", response_class=HTMLResponse)
def guide_alerts_page(request: Request, user=Depends(require_user)):
    """② 알람·운영 가이드 — 임계값/무응답/중복방지/해제/에스컬레이션/확인 동작 설명."""
    return templates.TemplateResponse(request, "guide_alerts.html", {"current_user": user})


@app.get("/guide/architecture", response_class=HTMLResponse)
def guide_arch_page(request: Request, user=Depends(require_user)):
    """③ 아키텍처 — 에이전트/중앙 스택/데이터 흐름/보안/라벨 체계."""
    return templates.TemplateResponse(request, "guide_arch.html", {"current_user": user})


_ACK_HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>장애 확인</title></head>
<body style="font-family:sans-serif; background:#faf6f3; margin:0; display:flex; min-height:100vh; align-items:center; justify-content:center;">
<div style="background:#fff; border:2px solid #ddcdc6; border-radius:12px; padding:36px 40px; max-width:440px; text-align:center; box-shadow:0 6px 24px rgba(0,0,0,.08);">
  <h1 style="color:{color}; font-size:22px; margin:0 0 12px;">{title}</h1>
  <p style="color:#5b4f49; font-size:15px; line-height:1.6; margin:0;">{msg}</p>
  {link}
</div></body></html>"""


@app.get("/ack/{token}", response_class=HTMLResponse)
def ack_incident_page(token: str, by: str = "", db: Session = Depends(get_db)):
    """이메일의 '확인' 버튼 — 비로그인 접근. 확인 시 에스컬레이션 중지. by=확인자(이메일)."""
    from src.db.crud import acknowledge_incident
    inc = acknowledge_incident(db, token, by=(by.strip() or None))
    if not inc:
        return HTMLResponse(_ACK_HTML.format(
            color="#b03a2e", title="유효하지 않은 링크",
            msg="확인 링크가 잘못되었거나 만료되었습니다.", link=""), status_code=404)
    when = inc.acknowledged_at.strftime("%Y-%m-%d %H:%M") if inc.acknowledged_at else ""
    who = f" ({inc.acknowledged_by})" if inc.acknowledged_by else ""
    link = (
        f'<a href="/incidents/{inc.id}" style="display:inline-block; margin-top:18px; '
        f'background:#c0564b; color:#fff; padding:10px 22px; border-radius:6px; '
        f'text-decoration:none; font-weight:600;">장애 상세 보기 →</a>'
        f'<p style="color:#a0998f; font-size:12px; margin-top:8px;">(로그인이 필요합니다)</p>'
    )
    return HTMLResponse(_ACK_HTML.format(
        color="#2f9e44", title="확인 완료 ✓",
        msg=f"장애 #{inc.id} — {inc.alarm_name or ''} 을(를) 확인{who} 처리했습니다.<br>추가 에스컬레이션이 중지됩니다. ({when})",
        link=link))


@app.get("/api/logs/raw")
async def api_logs_raw(host: str, hours: int = 24, download: int = 0, user=Depends(require_user)):
    """서버(host)의 최근 N시간 로그를 VictoriaLogs(7일 보관)에서 raw 텍스트로 반환.
    download=1이면 .log 파일로 첨부 다운로드. 장애와 무관하게 수동 열람용."""
    host = (host or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail="host 필요")
    from src.collector import victorialogs_collector
    try:
        lines = await victorialogs_collector.collect_range(host, hours=hours)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"VictoriaLogs 조회 실패: {e}")
    text = "\n".join(lines) if lines else f"(로그 없음 — host={host}, 최근 {hours}h. 보관 7일 이내인지 확인)"
    headers = {}
    if download:
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        safe = "".join(c for c in host if c.isalnum() or c in "-_.") or "host"
        headers["Content-Disposition"] = f'attachment; filename="{safe}_{stamp}.log"'
    return Response(content=text, media_type="text/plain; charset=utf-8", headers=headers)


# ── Webhook ──────────────────────────────────────────────────────────────────

@app.post("/webhook/alert", status_code=202)
async def webhook_alert(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """(정본·에이전트 기반) monitoring_msp Alertmanager(또는 vmalert) 알람 수신.
    firing 알람마다 incident 생성 → run_pipeline 이 VictoriaLogs에서 host 직전 5분 로그를 pull 해 AI 분석.

    보안: WEBHOOK_SECRET 설정 시 `Authorization: Bearer <secret>` 헤더 필수.
    (이 엔드포인트는 nginx로 공개 노출되므로 무인증이면 가짜 알람 주입 가능 → 토큰 검증)"""
    import hmac as _hmac
    if settings.webhook_secret:
        auth = request.headers.get("Authorization", "")
        if not _hmac.compare_digest(auth, f"Bearer {settings.webhook_secret}"):
            raise HTTPException(status_code=401, detail="invalid webhook token")
    payload = await request.json()
    alarms = parse_alertmanager_payload(payload)
    if not alarms:
        return {"status": "ignored", "reason": "no firing alerts with host label", "incident_ids": []}
    from src.db.crud import find_open_incident, resolve_incidents_for
    incident_ids = []
    for alarm_data in alarms:
        host = alarm_data.get("resourceName")
        name = alarm_data.get("alarmName")
        # resolved 통지 → 같은 host+알람의 미해결 incident 정리(에스컬레이션도 중지)
        if alarm_data.get("_resolved"):
            n = resolve_incidents_for(db, host, name)
            if n:
                logger.info("resolved 수신 — %s/%s incident %d건 resolved", host, name, n)
            continue
        # 중복 방지 — 같은 host+알람의 미해결 incident가 있으면 새로 만들지 않음
        existing = find_open_incident(db, host, name)
        if existing:
            logger.info("중복 알람 무시 — 기존 incident #%d 유지 (%s/%s)", existing.id, host, name)
            incident_ids.append(existing.id)
            continue
        incident = create_incident(db, alarm_data)
        background_tasks.add_task(run_pipeline, incident.id, alarm_data)
        incident_ids.append(incident.id)
    logger.info("webhook/alert — incident: %s", incident_ids)
    return {"status": "accepted", "incident_ids": incident_ids}


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


@app.delete("/api/notifications/{notif_id}")
def api_delete_notification(notif_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """알림 로그 1건 삭제 (관리자 전용)."""
    if not delete_notification(db, notif_id):
        raise HTTPException(status_code=404, detail="not found")
    return {"deleted": 1}


@app.delete("/api/notifications")
def api_delete_notifications(
    channel: str = "",
    status: str = "",
    q: str = "",
    date_from: str = "",
    date_to: str = "",
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    """현재 필터(없으면 전체)에 매칭되는 알림 로그를 일괄 삭제 (관리자 전용)."""
    n = delete_notifications_filtered(
        db,
        channel=channel.strip() or None,
        status=status.strip() or None,
        search=q.strip() or None,
        date_from=_parse_date(date_from),
        date_to=_parse_date(date_to, end_of_day=True),
    )
    return {"deleted": n}


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
        "architecture": s.architecture,
        "has_architecture": bool(s.architecture),
        "auto_created": s.auto_created,
        "enabled": s.enabled,
        "recipient_count": len(s.recipients) if s.recipients is not None else 0,
    }
    if include_recipients:
        out["recipients"] = [_recipient_to_dict(r) for r in (s.recipients or [])]
    return out


@app.get("/sites", response_class=HTMLResponse)
def sites_page(request: Request, db: Session = Depends(get_db), user=Depends(require_user)):
    # 활성 서버(host)를 사이트로 자동 등록 — 분석/알람 없이 로그·메트릭만 들어와도 바로 보이게.
    # (패턴 사이트가 있으면 그쪽으로 매칭, 없으면 host 이름으로 1:1 생성)
    try:
        from src.collector.victoriametrics_collector import list_active_host_groups
        from src.db.crud import sync_sites_from_hosts
        host_groups = list_active_host_groups()
        if host_groups:
            n = sync_sites_from_hosts(db, host_groups)
            if n:
                logging.getLogger(__name__).info("사이트 자동 동기화: %d개 신규 (활성 host %d)", n, len(host_groups))
                from src.alarm_rules import write_rules
                write_rules(db)   # 신규 서버 → 기본 임계값 알람 룰 생성
    except Exception as e:
        logging.getLogger(__name__).warning("사이트 자동 동기화 실패: %s", e)
    sites = list_sites(db)
    # 상위 그룹(=게이트웨이/공인IP 단위)으로 묶기 — group_name 없으면 '미분류'(빈 문자열)
    buckets: dict = {}
    for s in sites:
        buckets.setdefault(s.group_name or "", []).append(s)
    groups = []
    for gname, gsites in sorted(buckets.items(), key=lambda kv: (kv[0] == "", kv[0].lower())):
        groups.append({
            "name": gname,
            "sites": gsites,
            "count": len(gsites),
            "recipients": sum(len(s.recipients) for s in gsites),
            "enabled": sum(1 for s in gsites if s.enabled),
        })
    return templates.TemplateResponse(
        request, "sites.html",
        {"sites": sites, "groups": groups, "current_user": user},
    )


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


@app.put("/api/sites/{site_id}")
async def api_update_site(site_id: int, request: Request, db: Session = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    data = await request.json()
    site = update_site(db, site_id, data)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    from src.alarm_rules import write_rules
    write_rules(db)   # 임계값 변경 → 룰 재생성(vmalert 핫리로드)
    return _site_to_dict(site)


@app.delete("/api/sites/{site_id}")
def api_delete_site(site_id: int, db: Session = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    if not delete_site(db, site_id):
        raise HTTPException(status_code=404, detail="Site not found")
    from src.alarm_rules import write_rules
    write_rules(db)
    return {"deleted": site_id}


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


@app.delete("/api/sites/{site_id}/notes")
def api_delete_site_notes(site_id: int, author: str = "", db: Session = Depends(get_db), _admin=Depends(require_admin)) -> dict:
    """사이트 메모 일괄 삭제. ?author=ai 면 AI 메모만, 없으면 전체."""
    deleted = delete_site_notes(db, site_id, author=author or None)
    return {"deleted": deleted}


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
