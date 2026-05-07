# CLAUDE.md - AI Incident Response 프로젝트 작업 지침

## 프로젝트 개요

NCP 클라우드 인프라의 장애 알람을 Webhook으로 수신하여 로그를 자동 수집하고,
Claude AI로 원인을 분석 후 Slack으로 통보하는 FastAPI 기반 중앙 서버.

## 기술 스택

- Python 3.11 / FastAPI
- Claude API (`claude-sonnet-4-6`) — `src/analyzer/claude_client.py`
- SQLite (개발) / MySQL (프로덕션)
- Slack Webhook
- NCP Cloud Insight (알람 소스)

## 디렉토리 역할

```
src/
├── main.py              # FastAPI app, 라우터 등록
├── webhook/             # POST /webhook/alarm 수신 및 파싱
├── collector/           # 로그/메트릭 수집 (NCP API, SSH)
├── analyzer/            # Claude API 호출 및 프롬프트 관리
├── responder/           # Slack 알림, 이메일 전송
├── db/                  # SQLAlchemy 모델, CRUD
└── ui/                  # Jinja2 템플릿 또는 정적 파일
```

## 코딩 규칙

- 모든 외부 API 호출은 `async/await` 사용
- 환경변수는 `src/config.py` 에서 `pydantic-settings` 로 로드
- DB 모델은 `src/db/models.py`, CRUD는 `src/db/crud.py` 에 분리
- 에러 로그는 구조화된 JSON 형태 (`structlog` 또는 표준 logging)
- 테스트는 `tests/` 디렉토리, pytest 사용

## Claude API 사용 규칙

- 모델: `claude-sonnet-4-6` (기본값 유지)
- 프롬프트는 `src/analyzer/prompts.py` 에서 중앙 관리
- 로그 슬라이싱: 알람 시점 ±15분만 전달 (토큰 절약)
- system prompt는 SRE 역할 페르소나로 고정
- 응답은 반드시 JSON 구조로 강제 (tool use 또는 structured output 활용)

## Claude 응답 구조 (강제 포맷)

```json
{
  "cause_category": "리소스 부족 | 애플리케이션 오류 | 외부 서비스 장애 | 인프라 이슈",
  "cause_detail": "구체적 원인 설명",
  "severity": "Critical | High | Medium | Low",
  "impact_scope": "영향 범위 설명",
  "immediate_actions": ["조치1", "조치2", "조치3"],
  "prevention": "재발 방지 권고"
}
```

## 환경변수 (.env)

```
CLAUDE_API_KEY=
SLACK_WEBHOOK_URL=
NCP_ACCESS_KEY=
NCP_SECRET_KEY=
NCP_REGION=KR
WEBHOOK_SECRET=
DATABASE_URL=sqlite:///./incidents.db
```

## 주요 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| POST | `/webhook/alarm` | Cloud Insight 알람 수신 |
| GET | `/incidents` | 장애 목록 |
| GET | `/incidents/{id}` | 장애 상세 |
| GET | `/` | 웹 대시보드 |

## 작업 시 주의사항

- NCP API 키는 절대 코드에 하드코딩 금지
- Webhook 수신 시 HMAC 검증 로직 포함할 것
- 로그 수집 실패해도 AI 분석은 가능한 정보로 계속 진행 (graceful degradation)
- Claude API 호출 실패 시 Slack에 "AI 분석 실패, 수동 확인 요망" 알림 전송
