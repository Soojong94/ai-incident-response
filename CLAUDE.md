# CLAUDE.md - AI Incident Response 프로젝트 작업 지침

## 프로젝트 개요

인프라 장애 알람을 수신해 관련 로그를 자동 수집하고, AI로 원인을 분석한 뒤
웹 대시보드/이메일로 전달하는 FastAPI 기반 중앙 분석 서버.

**아키텍처 = 에이전트 기반(vendor-neutral) all-in-one.**
- 대상 서버엔 **Grafana Alloy** 하나만 설치 → 메트릭/로그를 중앙으로 상시 전송.
- 중앙(`docker-compose.yml`)은 **app · nginx · VictoriaMetrics · VictoriaLogs · vmalert · Alertmanager** 를 함께 구동.
- 흐름: vmalert 발화 → Alertmanager → (내부)`POST /webhook/alert` → VictoriaLogs 직전5분 pull → AI 분석 → DB/이메일.
- NCP-native(Cloud Insight/CLA/OBS/Cloud Function)는 **전부 제거됨**.
- 정본: [`docs/agent-based-pivot.md`](docs/agent-based-pivot.md) · [`README.md`](README.md) · [`docs/install-runbook.md`](docs/install-runbook.md).

## 기술 스택

- Python 3.11+ / FastAPI / SQLAlchemy
- Timely GPT Native API (`gpt-5.1`) — `src/analyzer/ai_client.py` (추후 Claude `claude-sonnet-4-6` 교체 — 이 파일만 수정)
- SQLite (개발) / MySQL (프로덕션)
- **로그**: VictoriaLogs (Alloy 상시 push, 7일 롤링) — 알람 시 직전 5분 pull
- **메트릭**: VictoriaMetrics (보관 3개월) — 활성 host/group 조회(사이트 자동등록)에도 사용
- **알람**: vmalert → Alertmanager → `POST /webhook/alert`
- **전송 보안**: 에이전트→중앙은 nginx 443(`/vm/`,`/vl/`) TLS + basic-auth

## 에이전트 (monitoring/alloy/)

| 역할 | config | 비고 |
|------|--------|------|
| 직접(direct) | `config.alloy` | 인터넷 O, 중앙으로 바로 전송 |
| 게이트웨이(gateway) | `gateway.alloy` | 직접 + 폐쇄망 중계 겸용. 자기+중계분에 `group` 라벨 |
| 폐쇄망(closed) | `closed.alloy` | 인터넷 X, 게이트웨이 사내 IP(9999/9998)로만 |

- 라벨: `host`(=서버=사이트), `group`(=상위 그룹, 게이트웨이/공인IP 단위).

## 디렉토리 역할

```
src/
├── main.py              # FastAPI app, 라우터 등록
├── config.py            # pydantic-settings 환경변수 로드
├── auth.py              # bcrypt 해시 + 세션 인증
├── webhook/handler.py   # POST /webhook/alert 파싱 + run_pipeline(로그수집→AI→알림)
├── collector/
│   ├── victorialogs_collector.py    # 알람 직전5분 / 7일 로그 조회
│   └── victoriametrics_collector.py # 활성 host+group 조회(사이트 자동등록)
├── analyzer/
│   ├── ai_client.py     # Timely GPT → Claude API 교체 시 이 파일만 수정
│   └── prompts.py       # 프롬프트 템플릿 중앙 관리
├── db/                  # models.py / crud.py / database.py(기동 시 컬럼 자동 마이그레이션)
└── ui/                  # Jinja2 템플릿 (웹 대시보드)
```

## 코딩 규칙

- 외부 API 호출은 `async/await` (`httpx.AsyncClient`). 단, 동기 라우트에서의 단발 조회는 `httpx.Client` 허용.
- 환경변수는 `src/config.py` 에서 `pydantic-settings` 로 로드
- DB 모델은 `src/db/models.py`, CRUD는 `src/db/crud.py`. 컬럼 추가 시 `database.py` 마이그레이션 목록에도 등록.
- 에러 로그는 표준 logging. 로그 수집/외부 호출 실패는 graceful degradation.
- 테스트는 `tests/`, pytest. 변경 후 최소 `py_compile` + 템플릿 컴파일 + app import 확인.
- 에이전트 config(`.alloy`) 수정 시 **한 줄에 속성 1개**(River 문법) — `alloy fmt`로 검증.

## AI API 사용 규칙 (Timely GPT Native API)

- 파일: `src/analyzer/ai_client.py`, 모델 `gpt-5.1`
- 인증: `GET /sdk-auth/authenticate` → JWT 교환 후 Bearer (유효 ~55분, 클래스 내 캐시)
- 엔드포인트: `POST /llm-completion`, 응답 파싱 `response["parsed"]`
- 프롬프트는 `src/analyzer/prompts.py` 중앙 관리, 로그는 `preprocess_logs`로 토큰 절약
- 구조화 응답: `output_type:"JSON"` + `output_schema`, 언어 `"locale":"ko"`
- 동시 분석 1건(Semaphore) + 내부 1회 재시도, 실패 시 incident `ai_failed`

**Claude API로 교체 시:** Bearer API Key 직접 사용, Anthropic SDK/호환 엔드포인트, Tool Use/`response_format` 구조화, `choices[0].message.content`/tool input 파싱.

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

## 환경변수 (.env — `.env.example` 참고)

```
# AI API (Timely GPT — 추후 Claude로 교체)
AI_API_KEY=tgpt_sk_xxx
AI_BASE_URL=https://hello.timelygpt.co.kr/api/v2/chat
AI_MODEL=gpt-5.1
# Claude로 교체 시: AI_API_KEY=sk-ant-xxx / AI_BASE_URL=https://api.anthropic.com/v1 / AI_MODEL=claude-sonnet-4-6

DATABASE_URL=sqlite:///./data/incidents.db

# 관측 소스 (컨테이너 내부 주소)
VICTORIALOGS_URL=http://victorialogs:9428
VICTORIAMETRICS_URL=http://victoriametrics:8428
LOG_WINDOW_SECONDS=300

# Webhook 인증 토큰(선택) — 분리 배포 시 Authorization: Bearer. all-in-one은 nginx가 공개 차단.
WEBHOOK_SECRET=

# SMTP / 인증 / admin — .env.example 참고
```

> 에이전트 인제스트 basic-auth 자격은 `.env`가 아니라 `nginx/ingest.htpasswd`(gitignore)로 관리.

## 주요 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| POST | `/webhook/alert` | (정본) Alertmanager 알람 수신 → 로그 pull → AI 분석. nginx에서 외부 차단 |
| GET | `/` , `/incidents/{id}` | 장애 목록 / 상세 |
| GET | `/sites` , `/sites/{id}` | 사이트(그룹) 관리 / 사이트 상세 |
| GET | `/api/logs/raw` | 서버 7일 로그 열람/다운로드 (`?host=&hours=&download=`) |
| GET/DELETE | `/notifications` , `/api/notifications[...]` | 알림 발송 로그 / 삭제(admin) |
| GET | `/stats` , `/admin/users` , `/guide` | 통계 / 사용자 / 사용 가이드 |

## 작업 시 주의사항

- VictoriaMetrics/VictoriaLogs(8428/9428)는 무인증 — 절대 공개 금지. 외부 인제스트는 nginx 443(`/vm/`,`/vl/`)만.
- 로그 수집 실패해도 AI 분석은 가능한 정보로 계속 진행(graceful degradation).
- AI 호출 실패 시 incident `ai_failed`로 업데이트.
- Webhook은 즉시 202 반환 → 분석은 `BackgroundTasks` 비동기 처리.
- Timely 응답 최대 ~90초 — `httpx` timeout 설정.
- 배포 반영: `git pull && docker compose up -d --build && docker compose restart nginx` (app 재생성 후 nginx IP 캐시 때문에 restart 필요).
