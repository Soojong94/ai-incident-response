from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AI API (Timely GPT or Claude)
    ai_api_key: str = ""
    ai_base_url: str = "https://hello.timelygpt.co.kr/api/v2/chat"
    ai_model: str = "claude-sonnet-4-6"

    # Database
    database_url: str = "sqlite:///./incidents.db"

    # Webhook 인증 토큰 — 설정 시 /webhook/alert 에 `Authorization: Bearer <token>` 필수
    webhook_secret: str = ""

    # VictoriaLogs (에이전트 기반 — 알람 시 host 직전 N분 로그 pull)
    # 운영: 같은 사설 subnet의 monitoring_msp VictoriaLogs 사설 IP. 무인증이므로 공인 노출 금지.
    victorialogs_url: str = "http://localhost:9428"
    log_window_seconds: int = 300  # 알람 직전 5분
    # VictoriaMetrics — 활성 host 목록 조회(사이트 자동 등록)용. 내부 사설 hop.
    victoriametrics_url: str = "http://localhost:8428"
    # vmalert 룰 디렉토리 — 사이트별 알람 임계값을 generated.yml로 써서 vmalert가 핫리로드
    vmalert_rules_dir: str = "/etc/vmalert/rules"
    # 서버 무응답(dead-man) 감지 — N초 이상 메트릭 미수신이면 "서버 무응답" 장애 자동 등록
    deadman_enabled: bool = True
    deadman_seconds: int = 180          # 3분
    deadman_check_interval: int = 60    # 점검 주기(초)

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

    # 공개 베이스 URL (대시보드)
    public_base_url: str = "https://tbit-msp.kro.kr"


settings = Settings()
