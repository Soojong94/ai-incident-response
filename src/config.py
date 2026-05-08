from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AI API (Timely GPT or Claude)
    ai_api_key: str = ""
    ai_base_url: str = "https://hello.timelygpt.co.kr/api/v2/chat/bridge/openai"
    ai_model: str = "anthropic/claude-sonnet-4-6"

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


settings = Settings()
