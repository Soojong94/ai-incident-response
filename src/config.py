from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AI API (Timely GPT or Claude)
    ai_api_key: str = ""
    ai_base_url: str = "https://hello.timelygpt.co.kr/api/v2/chat"
    ai_model: str = "gpt-5.1"

    # Database
    database_url: str = "sqlite:///./incidents.db"

    # Webhook HMAC (optional)
    webhook_secret: str = ""

    # NCP
    ncp_access_key: str = ""
    ncp_secret_key: str = ""
    ncp_region: str = "KR"

    # NCP Object Storage
    obs_bucket: str = "team1-demo"

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


settings = Settings()
