# AI 기반 장애 대응 자동화 (AI Incident Response)

> 인프라 장애 알람을 받아 **직전 5분 로그를 자동 수집**하고, **AI가 원인을 분석**해
> 웹 대시보드와 이메일로 전달하는 중앙 분석 서버. 멀티클라우드(NCP/AWS/KT/온프렘) 공통.

---

## 1. 개요

- 대상 서버에는 **에이전트(Grafana Alloy) 하나만** 설치합니다. 메트릭(CPU/메모리/디스크)과 로그를 중앙으로 상시 전송합니다.
- 중앙에서 임계값(예: CPU 80% 5분)을 넘으면 **Alertmanager가 발화** → 분석 서버가 그 host의 **직전 5분 로그를 VictoriaLogs에서 pull** → **AI가 원인/조치/심각도**를 분석 → **대시보드 + 이메일/Slack**으로 전달합니다.
- 클라우드 종속 요소(NCP Cloud Insight/CLA/OBS/Cloud Function)는 **전부 제거**되었습니다. 서버에서 인터넷(HTTPS)으로 나가는 길만 있으면 어디든 동일하게 동작합니다.

> 아키텍처 정본: [`docs/agent-based-pivot.md`](docs/agent-based-pivot.md) · 설치 절차: [`docs/install-runbook.md`](docs/install-runbook.md) · 다이어그램: [`docs/architecture.drawio`](docs/architecture.drawio)

---

## 2. 아키텍처

```
                    [고객/대상 서버]
   ┌─ 직접(direct)      Alloy ─────────── HTTPS(443, TLS+basic-auth) ──┐
   │   인터넷 O                                                          │
   │                                                                     ▼
   ├─ 게이트웨이(gateway) Alloy(자기수집 + 폐쇄망 중계) ── HTTPS ──►  [중앙 분석 서버]
   │   인터넷 O   ▲ 사내망 9999/9998                                  nginx(443)
   │             │                                                    ├─ /vm/ → VictoriaMetrics(8428)
   └─ 폐쇄망(closed)    Alloy ── 사내망 ──┘                            ├─ /vl/ → VictoriaLogs(9428, 7일)
       인터넷 X                                                        └─ /   → app(FastAPI)
                                                                        vmalert(8880) → Alertmanager(9093)
                                                                        Alertmanager ──webhook(내부)──► app /webhook/alert
                                                                        app → VictoriaLogs 직전5분 pull → AI 분석 → DB/이메일
```

### 에이전트 3가지 역할 (종류는 2개 + 역할 1개)
| 역할 | 인터넷 | 설명 | config |
|------|:---:|------|--------|
| **직접 (direct)** | O | 중앙으로 바로 전송하는 일반 서버 | [`monitoring/alloy/config.alloy`](monitoring/alloy/config.alloy) |
| **게이트웨이 (gateway)** | O | 직접 서버 중 **폐쇄망 서버의 중계까지 겸하는 1대**(= 공인IP/그룹 단위) | [`monitoring/alloy/gateway.alloy`](monitoring/alloy/gateway.alloy) |
| **폐쇄망 (closed)** | X | 인터넷이 없어 **게이트웨이 경유**로만 보내는 서버 | [`monitoring/alloy/closed.alloy`](monitoring/alloy/closed.alloy) |

- 에이전트 "종류"는 **직접 / 폐쇄망 2가지**이고, 게이트웨이는 **직접이 중계 역할을 겸한 것**입니다.
- 폐쇄망 서버는 게이트웨이로만 나가므로 **중앙 방화벽엔 게이트웨이 IP만** 열면 됩니다.

### 데이터 흐름
1. 에이전트가 메트릭(15s)·로그를 **HTTPS 443**(`/vm/`, `/vl/`)으로 상시 전송 — nginx가 TLS 종단 + basic-auth 후 내부 VictoriaMetrics/VictoriaLogs로 전달.
2. 중앙 **vmalert**가 룰 평가 → 임계 초과 시 **Alertmanager**로 알림.
3. Alertmanager가 **내부에서** `POST /webhook/alert` 호출(공개 차단).
4. 분석 서버가 해당 host의 **직전 5분 로그를 VictoriaLogs에서 pull**.
5. **AI 분석**(원인 분류/상세/심각도/영향/즉시조치/재발방지) → DB 저장 → 대시보드 + 이메일/Slack.

---

## 3. 구성요소 (중앙 — `docker-compose.yml`)

| 서비스 | 포트 | 역할 |
|--------|------|------|
| `app` | 8000(내부) | FastAPI — webhook 수신, 로그 pull, AI 분석, 대시보드 |
| `nginx` | 80/443 | TLS 종단, `/vm/`·`/vl/` 인제스트 리버스프록시(basic-auth), `/webhook/` 공개 차단 |
| `victoriametrics` | 8428(내부) | 메트릭 저장(보관 3개월) |
| `victorialogs` | 9428(내부) | 로그 저장(보관 **7일** 롤링) |
| `vmalert` | 8880(내부) | 알람 룰 평가 → Alertmanager |
| `alertmanager` | 9093(내부) | dedup/그룹핑 → `app /webhook/alert` |

> 8428/9428은 **무인증**이라 절대 공개하지 않습니다. 외부 인제스트는 **443(`/vm/`,`/vl/`) + basic-auth**로만.

---

## 4. 보안

- **전송 구간 TLS**: 에이전트→중앙은 평문 8428/9428이 아니라 **HTTPS 443**(`/vm/`,`/vl/`) 경유 — 로그가 평문으로 인터넷을 타지 않음. nginx가 **basic-auth**(`nginx/ingest.htpasswd`)로 검증.
- **webhook 비공개**: `/webhook/alert`는 nginx에서 외부 차단(`return 403`). Alertmanager는 내부에서 직접 호출. 분리 배포 시엔 `WEBHOOK_SECRET`로 `Authorization: Bearer` 토큰 검증.
- **로그인/세션**: 비밀번호 **bcrypt 해시**, 서명 쿠키(Secure), 약한 SESSION_SECRET/기본 비번 기동 시 경고.
- **무인증 포트**: VictoriaMetrics/Logs는 컨테이너 내부 네트워크에서만 접근. 공인 노출 금지.
- **방화벽**: 중앙은 **443만** 공개(에이전트 소스 IP로 제한 권장). 게이트웨이 사내망은 폐쇄망 서버에 9999/9998만 허용.

---

## 5. 대시보드 기능

### 사이트 & 그룹 (`/sites`)
- **서버(host) = 사이트**: 데이터(메트릭/로그)가 들어오면 **자동으로 사이트 등록**(분석/장애 없이도 바로). 서버별로 수신자·임계정책을 관리.
- **상위 그룹(드릴인)**: 같은 게이트웨이(=공인IP) 뒤의 서버들을 **그룹 카드 1개**로 묶어 보여주고, **카드를 클릭하면 그 그룹의 서버들**로 들어갑니다(`GROUP_NAME` 라벨 기준).
- **수신자**: 사이트별 이메일/Slack + **심각도별 수신 토글**(Critical/High/Medium/Low).
- **매칭 패턴**: 여러 서버를 한 사이트로 묶을 때 글로브 패턴(예 `web-prod-*`). 없으면 host 이름으로 1:1 자동 생성.
- **알림 폭주 방지(rate-limit)**: 윈도우 안 임계 초과분은 분석/알림 자동 억제(기본 5분 3건), 비상 모드로 끌 수 있음.
- **아키텍처 메모**: 사이트 인프라 구성을 적어두면 AI 분석 prompt에 포함되어 정확도↑.
- **서버 로그 다운로드**: 사이트 상세에서 그 서버의 **최근 7일 raw 로그**를 시간대별로 미리보기/`.log` 다운로드(`GET /api/logs/raw`).
- **사이트 메모 + 학습**: AI가 반복 원인을 누적, 👍 피드백 시 검증 메모로 승격 → 다음 분석에 활용.

### 장애 (`/`, `/incidents/{id}`)
- 장애 목록(발생 서버·심각도·AI 원인·상태), 상세에서 **수집 로그 + AI 분석 전문**.
- 상태: `processing`(분석중) → `analyzed`(완료) / `ai_failed`(분석실패) / `suppressed`(폭주 억제).

### 운영
- **알림 발송 로그**(`/notifications`, admin): 발송 이력 + **개별/필터 일괄 삭제**.
- **통계**(`/stats`, admin), **사용자 관리**(`/admin/users`, admin), **비밀번호 재설정**(메일).

---

## 6. 설치 (요약 — 상세는 [`docs/install-runbook.md`](docs/install-runbook.md))

### 중앙 서버
```bash
cd /opt/ai-incident-response && git pull
# 에이전트 인제스트 basic-auth 자격 생성 (최초 1회)
htpasswd -bc nginx/ingest.htpasswd agent '<강한-비밀번호>'   # 없으면: apt-get install -y apache2-utils
docker compose up -d --build && docker compose restart nginx
```
- ACG: 에이전트 공인 IP로 **443**만 허용. (8428/9428 비공개)

### 에이전트 (대상 서버) — 대시보드 `/guide` 참고
1. Alloy 설치(바이너리 1개).
2. `config.alloy` 작성 — `RESOURCE_NAME`(서버 이름) / `GROUP_NAME`(그룹) / `INGEST_USER`·`INGEST_PASS` / 중앙 `/vm/`·`/vl/` URL.
3. systemd 등록 후 실행. **보내기 시작하면 중앙이 바로 수신** → 사이트 관리에 자동 등장.
- 폐쇄망 서버는 [`closed.alloy`](monitoring/alloy/closed.alloy)로 게이트웨이 사내 IP(9999/9998)에만 전송.

---

## 7. 환경변수 (`.env` — [`.env.example`](.env.example))

| 변수 | 설명 |
|------|------|
| `AI_API_KEY` | AI API 키 (Timely `tgpt_sk_…` 또는 Claude `sk-ant-…`) |
| `AI_BASE_URL` / `AI_MODEL` | AI 엔드포인트 / 모델 (`gpt-5.1` 또는 `claude-sonnet-4-6`) |
| `DATABASE_URL` | DB 연결 (기본 `sqlite:///./data/incidents.db`) |
| `VICTORIALOGS_URL` / `VICTORIAMETRICS_URL` | 내부 VictoriaLogs/Metrics 주소(컨테이너) |
| `LOG_WINDOW_SECONDS` | 알람 시 수집할 직전 로그 구간(기본 300=5분) |
| `WEBHOOK_SECRET` | (선택) `/webhook/alert` Bearer 토큰 — 분리 배포 시 |
| `SMTP_*` / `ALERT_EMAIL` | 이메일 발송 + 수신자 미등록 시 폴백 주소 |
| `SESSION_SECRET` / `SESSION_COOKIE_SECURE` | 세션 서명키 / HTTPS 쿠키 |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | 최초 admin 자동 생성 |

> AI 제공자 교체는 [`src/analyzer/ai_client.py`](src/analyzer/ai_client.py) 한 파일만 수정.

---

## 8. 주요 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| POST | `/webhook/alert` | (정본) Alertmanager 알람 수신 → 로그 pull → AI 분석. 외부 비공개 |
| GET | `/` , `/incidents/{id}` | 장애 목록 / 상세 |
| GET | `/sites` , `/sites/{id}` | 사이트(그룹) 관리 / 사이트 상세 |
| GET | `/api/logs/raw?host=&hours=&download=` | 서버 7일 로그 열람/다운로드 |
| GET/DELETE | `/notifications` , `/api/notifications[...]` | 알림 발송 로그 / 삭제(admin) |
| GET | `/stats` , `/admin/users` , `/guide` | 통계 / 사용자 / 사용 가이드 |

---

## 9. 운영

- **배포 반영**: `git pull && docker compose up -d --build && docker compose restart nginx`
  (app 컨테이너 재생성 후 nginx가 옛 IP를 캐시하므로 nginx restart 필요)
- **테스트 데이터 정리**: [`scripts/cleanup.sh`](scripts/cleanup.sh) — 장애 내역 + 연동 AI 메모 삭제.
- DB 스키마는 기동 시 자동 마이그레이션(컬럼 추가)됩니다.

---

## 10. 기술 스택 / 디렉토리

| 항목 | 내용 |
|------|------|
| Backend | Python 3.11+ / FastAPI / SQLAlchemy |
| AI | Timely GPT `gpt-5.1` (→ Claude `claude-sonnet-4-6` 교체 예정) |
| DB | SQLite(개발) / MySQL(운영) |
| 관측 | Grafana Alloy(에이전트) · VictoriaMetrics · VictoriaLogs · vmalert · Alertmanager |

```
src/
├── main.py              # FastAPI 앱, 라우터
├── config.py            # pydantic-settings 환경변수
├── auth.py              # bcrypt 해시 + 세션 인증
├── webhook/handler.py   # /webhook/alert 파싱 + run_pipeline(로그수집→AI→알림)
├── collector/
│   ├── victorialogs_collector.py    # 알람 시 직전5분 / 7일 로그 조회
│   └── victoriametrics_collector.py # 활성 host+group 조회(사이트 자동등록)
├── analyzer/{ai_client.py, prompts.py}   # AI 호출 + 프롬프트
├── db/{models.py, crud.py, database.py}  # 모델/CRUD/마이그레이션
└── ui/templates/        # Jinja2 대시보드
monitoring/alloy/        # config.alloy(직접)/gateway.alloy/closed.alloy + docker·windows 오버레이
monitoring/{vmalert,alertmanager}/       # 알람 룰 / 라우팅
nginx/nginx.conf         # TLS + /vm/ /vl/ 인제스트 + webhook 차단
docs/install-runbook.md  # 처음부터 설치 런북(web+was 예시)
```
