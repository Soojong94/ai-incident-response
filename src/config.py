from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AI API (Timely GPT or Claude)
    ai_api_key: str = ""
    ai_base_url: str = "https://hello.timelygpt.co.kr/api/v2/chat"
    ai_model: str = "claude-sonnet-4-6"

    # Database
    database_url: str = "sqlite:///./incidents.db"

    # Webhook HMAC (optional)
    webhook_secret: str = ""

    # VictoriaLogs (에이전트 기반 — 알람 시 host 직전 N분 로그 pull)
    # 운영: 같은 사설 subnet의 monitoring_msp VictoriaLogs 사설 IP. 무인증이므로 공인 노출 금지.
    victorialogs_url: str = "http://localhost:9428"
    log_window_seconds: int = 300  # 알람 직전 5분

    # SMTP
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    alert_email: str = ""

    # Auth
    session_secret: str = "dev-insecure-change-me"
    session_cookie_secure: bool = False        # 운영(HTTPS)에선 True 권장
    session_max_age_seconds: int = 60 * 60 * 8  # 8시간
    min_password_length: int = 8
    admin_email: str = "admin@example.com"
    admin_password: str = "changeme"

    # Encryption — site별 NCP/CF 키 저장 시 대칭 암호화. .env에 32-byte url-safe base64 키 등록.
    # 미설정 시 dev key 사용 (위험 — 운영 배포 시 반드시 새로 생성).
    encryption_key: str = ""

    # 공개 베이스 URL — CF 코드 가이드에서 webhook URL 만들 때 사용 (예: https://tbit-msp.kro.kr)
    public_base_url: str = "https://tbit-msp.kro.kr"


settings = Settings()
