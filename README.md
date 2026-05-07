# AI 기반 장애 대응 자동화 (AI Incident Response)

> 클라우드 MSP 환경에서 발생하는 장애를 AI가 자동으로 분석하고 대응 방안을 제시하는 시스템

## 개요

NCP Cloud Insight 등 클라우드 모니터링 서비스의 알람을 Webhook으로 수신하여,
관련 로그를 자동 수집하고 Claude AI가 원인을 분석 후 Slack으로 즉시 통보하는 중앙 서버 기반 시스템.

## 핵심 기능

- Cloud Insight / CloudWatch Webhook 수신
- 알람 전후 로그 자동 수집 (NCP Log Analytics API / SSH)
- Claude AI 원인 분석 및 조치 권고
- Slack 실시간 알림
- 웹 UI 기반 장애 이력 관리

## 기술 스택

- **Backend**: Python 3.11 / FastAPI
- **AI**: Claude API (claude-sonnet-4-6)
- **DB**: SQLite (MVP) → MySQL (프로덕션)
- **알림**: Slack Webhook
- **대상 클라우드**: NCP (Naver Cloud Platform)

## 디렉토리 구조

```
ai-incident-response/
├── README.md
├── PRD.md                    # 제품 요구사항 문서
├── CLAUDE.md                 # Claude Code 작업 지침
├── docs/
│   ├── architecture.md       # 시스템 아키텍처
│   └── agent-design.md       # AI 에이전트 설계
└── src/
    ├── main.py               # FastAPI 진입점
    ├── webhook/              # Webhook 수신
    ├── collector/            # 로그/메트릭 수집
    ├── analyzer/             # Claude AI 분석
    ├── responder/            # Slack/Email 대응
    ├── db/                   # 장애 이력 저장
    └── ui/                   # 웹 대시보드
```

## 빠른 시작

```bash
cd src
pip install -r requirements.txt
cp .env.example .env   # 환경변수 설정
uvicorn main:app --reload
```

## 환경변수

| 변수 | 설명 |
|------|------|
| `CLAUDE_API_KEY` | Anthropic API 키 |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |
| `NCP_ACCESS_KEY` | NCP API Access Key |
| `NCP_SECRET_KEY` | NCP API Secret Key |
| `WEBHOOK_SECRET` | Cloud Insight Webhook 검증 시크릿 |
