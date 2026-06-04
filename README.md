# AI 기반 장애 대응 자동화 (AI Incident Response)

> 클라우드 MSP 환경에서 발생하는 장애를 AI가 자동으로 분석하고 대응 방안을 제시하는 시스템

## 개요

클라우드 인프라 장애 알람을 수신하여 관련 로그를 자동 수집하고,
AI가 원인을 분석 후 웹 대시보드/이메일로 전달하는 중앙 분석 서버.

> ⚠ **아키텍처 전환 (2026-06-04, vendor-neutral).** NCP-native 수집(Cloud Insight + CLA + OBS + Cloud Function)을
> **에이전트 기반**으로 전환했습니다. 메트릭/알람/에이전트는 별도 시스템 `monitoring_msp`(Grafana Alloy →
> VictoriaMetrics + vmalert + Alertmanager)를 재사용하고, 이 레포는 **"Alertmanager 알람 → VictoriaLogs 직전 5분
> 쿼리 → AI 분석 → 대시보드/이메일"** 을 담당합니다. **OBS·Cloud Function은 제거**(NCP 종속 소거).
> 정본: [`docs/agent-based-pivot.md`](docs/agent-based-pivot.md) §12 · 다이어그램 [`docs/architecture.drawio`](docs/architecture.drawio).

## 핵심 기능

- monitoring_msp Alertmanager 알람 수신 (`POST /webhook/alert`)
- 알람 발생 host의 **직전 5분 로그 자동 수집** (중앙 VictoriaLogs 쿼리)
- AI 원인 분석 및 조치 권고 (Timely GPT `gpt-5.1` → 추후 Claude API)
- 웹 UI 기반 장애 이력 관리 및 AI 분석 결과 조회 + 이메일 알림
- (레거시) Cloud Insight Webhook(`/webhook/alarm`) + OBS-CF 경로 — 폐기 예정

## 기술 스택

| 항목 | 내용 |
|------|------|
| Backend | Python 3.11 / FastAPI |
| AI | Timely GPT Native API (`gpt-5.1`) → 추후 Claude API 교체 |
| DB | SQLite (MVP) → MySQL (프로덕션) |
| 로그 소스 | VictoriaLogs (Grafana Alloy 상시 push, 7일 롤링) |
| 알람 소스 | monitoring_msp Alertmanager |
| 대상 클라우드 | 멀티클라우드 (NCP / AWS / KT / 온프렘 — 에이전트 기반) |

## 디렉토리 구조

```
ai-incident-response/
├── requirements.txt
├── .env.example
├── CLAUDE.md                 # Claude Code 작업 지침
├── PRD.md                    # 제품 요구사항 문서
├── docs/
│   ├── architecture.md       # 시스템 아키텍처
│   ├── agent-design.md       # AI 에이전트 설계
│   └── integration-notes.md  # API 테스트 결과 및 통합 노트
├── scripts/
│   └── test_api.py           # API 연결 테스트
├── src/
│   ├── main.py               # FastAPI 진입점
│   ├── config.py             # 환경변수 설정
│   ├── webhook/              # Webhook 수신
│   ├── collector/            # 로그/메트릭 수집
│   ├── analyzer/             # AI 분석
│   ├── db/                   # 장애 이력 저장
│   └── ui/                   # 웹 대시보드
└── tests/
```

## 빠른 시작

```bash
pip install -r requirements.txt
cp .env.example .env   # 환경변수 설정
uvicorn src.main:app --reload
```

## 환경변수

| 변수 | 설명 |
|------|------|
| `AI_API_KEY` | Timely GPT API 키 (`tgpt_sk_xxx`) 또는 Claude API 키 |
| `AI_BASE_URL` | AI API Base URL |
| `AI_MODEL` | 사용할 모델 (`gpt-5.1` 또는 `claude-sonnet-4-6`) |
| `NCP_ACCESS_KEY` | NCP API Access Key |
| `NCP_SECRET_KEY` | NCP API Secret Key |
| `WEBHOOK_SECRET` | Cloud Insight Webhook 검증 시크릿 (선택) |
| `DATABASE_URL` | DB 연결 문자열 (기본: SQLite) |

## 개발용 테스트 알람 발생

```bash
curl -X POST "http://localhost:8000/test/trigger?metric_type=CPU&resource_name=server-prod-01&current_value=95.3"
```
