# CLAUDE.md - AI Incident Response 프로젝트 작업 지침

## 프로젝트 개요

NCP 클라우드 인프라의 장애 알람을 Webhook으로 수신하여 로그를 자동 수집하고,
AI로 원인을 분석 후 웹 대시보드에서 확인하는 FastAPI 기반 중앙 서버.
(Slack 알림은 Phase 2)

## 기술 스택

- Python 3.11 / FastAPI
- Timely GPT Native API (`gpt-5.1`) — `src/analyzer/ai_client.py`
  - 추후 Claude API (`claude-sonnet-4-6`)로 교체 예정 — 해당 파일 내부만 수정
- SQLite (개발) / MySQL (프로덕션)
- NCP Cloud Insight (알람 소스)
- NCP Cloud Log Analytics (로그 수집 — 현재 Mock, 키 확보 후 교체)

## 디렉토리 역할

```
src/
├── main.py              # FastAPI app, 라우터 등록
├── config.py            # pydantic-settings 환경변수 로드
├── webhook/             # POST /webhook/alarm 수신 및 파싱
├── collector/           # 로그/메트릭 수집 (현재 mock_collector.py)
├── analyzer/            # AI API 호출 및 프롬프트 관리
│   ├── ai_client.py     # Timely GPT → Claude API 교체 시 이 파일만 수정
│   └── prompts.py       # 프롬프트 템플릿 중앙 관리
├── db/                  # SQLAlchemy 모델, CRUD
└── ui/                  # Jinja2 템플릿 (웹 대시보드)
```

## 코딩 규칙

- 모든 외부 API 호출은 `async/await` 사용 (`httpx.AsyncClient`)
- 환경변수는 `src/config.py` 에서 `pydantic-settings` 로 로드
- DB 모델은 `src/db/models.py`, CRUD는 `src/db/crud.py` 에 분리
- 에러 로그는 구조화된 JSON 형태 (`structlog` 또는 표준 logging)
- 테스트는 `tests/` 디렉토리, pytest 사용

## AI API 사용 규칙 (Timely GPT Native API)

- 파일: `src/analyzer/ai_client.py`
- 모델: `gpt-5.1` (Timely 기본 모델, 제한 없음)
- 인증: `GET /sdk-auth/authenticate` → JWT 교환 후 Bearer 사용 (유효 ~55분)
- 엔드포인트: `POST /llm-completion`
- 프롬프트는 `src/analyzer/prompts.py` 에서 중앙 관리
- 로그 슬라이싱: 알람 시점 ±15분만 전달 (토큰 절약)
- `instructions` 필드에 SRE 역할 페르소나 고정
- 구조화 응답: `output_type: "JSON"` + `output_schema` 로 강제
- 응답 파싱: `response["parsed"]` (dict)
- 응답 언어: `"locale": "ko"` 파라미터 필수

**Claude API로 교체 시 변경점:**
- 인증: Bearer API Key 직접 사용 (JWT 교환 불필요)
- 엔드포인트: Anthropic SDK 또는 OpenAI 호환 엔드포인트
- 구조화 응답: Tool Use 또는 `response_format`으로 전환
- 응답 파싱: `choices[0].message.content` 또는 tool input으로 전환

## AI 응답 구조 (강제 포맷)

```json
{
  "cause_category": "리소스 부족 | 애플리케이션 오류 | 외부 서비스 장애 | 인프라 이슈",
  "cause_detail": "구체적 원인 설명 (2-3문장)",
  "severity": "Critical | High | Medium | Low",
  "impact_scope": "영향 범위 설명",
  "immediate_actions": ["조치1", "조치2", "조치3"],
  "prevention": "재발 방지 권고",
  "confidence": "높음 | 보통 | 낮음"
}
```

## 환경변수 (.env)

```
# AI API (Timely GPT — 추후 Claude로 교체)
AI_API_KEY=tgpt_sk_xxx
AI_BASE_URL=https://hello.timelygpt.co.kr/api/v2/chat
AI_MODEL=gpt-5.1

# Claude API로 교체 시:
# AI_API_KEY=sk-ant-xxx
# AI_BASE_URL=https://api.anthropic.com/v1
# AI_MODEL=claude-sonnet-4-6

# Database
DATABASE_URL=sqlite:///./incidents.db

# Webhook HMAC (선택 — 없으면 검증 생략)
WEBHOOK_SECRET=

# NCP (현재 Mock — 키 확보 후 실제 수집기 교체)
NCP_ACCESS_KEY=
NCP_SECRET_KEY=
NCP_REGION=KR
```

## 주요 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| POST | `/webhook/alarm` | Cloud Insight 알람 수신 |
| POST | `/test/trigger` | 개발용 테스트 알람 발생 |
| GET | `/` | 웹 대시보드 (장애 목록) |
| GET | `/incidents/{id}` | 장애 상세 |

## 작업 시 주의사항

- NCP API 키는 절대 코드에 하드코딩 금지
- Webhook 수신 시 HMAC 검증 로직 포함 (`WEBHOOK_SECRET` 미설정 시 검증 생략)
- 로그 수집 실패해도 AI 분석은 가능한 정보로 계속 진행 (graceful degradation)
- AI API 호출 실패 시 incident status를 `ai_failed`로 업데이트, 웹 대시보드에서 확인 가능하게 처리
- Webhook은 즉시 202 반환 → 분석은 `BackgroundTasks`로 비동기 처리
- Timely API 응답 시간 최대 60초 — `httpx.AsyncClient` timeout 설정 필요
