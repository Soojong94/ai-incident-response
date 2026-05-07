# 시스템 아키텍처

## 전체 흐름

```
┌──────────────────────────────────────────────────────┐
│                   NCP 클라우드 인프라                  │
│  [고객사 서버]  →  [Cloud Insight]  →  Alarm 발생     │
└─────────────────────────┬────────────────────────────┘
                          │ HTTP POST (Webhook)
                          ↓
┌──────────────────────────────────────────────────────┐
│                    중앙 서버 (FastAPI)                  │
│                                                       │
│  ┌─────────────┐   ┌──────────────┐   ┌───────────┐  │
│  │  Webhook    │→  │  Collector   │→  │ Analyzer  │  │
│  │  Receiver   │   │  (Mock/NCP)  │   │(Timely AI)│  │
│  └─────────────┘   └──────────────┘   └─────┬─────┘  │
│         │                 │                  │        │
│         ↓                 ↓                  ↓        │
│  ┌──────────────────────────────────────────────────┐ │
│  │                    DB (SQLite)                    │ │
│  │  incidents / incident_logs / analysis_results     │ │
│  └──────────────────────────────────────────────────┘ │
│                                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │              Web UI (대시보드)                   │  │
│  │  장애 목록 / 상세 / AI 분석 결과                  │  │
│  └─────────────────────────────────────────────────┘  │
│                                                       │
│  [ Slack Responder — Phase 2 ]                        │
└──────────────────────────────────────────────────────┘
```

---

## 컴포넌트 상세

### 1. Webhook Receiver (`src/webhook/`)

- 엔드포인트: `POST /webhook/alarm`
- HMAC-SHA256 서명 검증 (`WEBHOOK_SECRET` 설정 시)
- 알람 페이로드 파싱 및 정규화 (camelCase/snake_case 모두 수용)
- `Incident` 레코드 DB 생성 (status: `processing`)
- `BackgroundTasks`로 비동기 파이프라인 즉시 시작 → 202 반환

**Cloud Insight Alarm 페이로드 예시**
```json
{
  "alarmName": "CPU-High-Alert",
  "resourceName": "web-01",
  "metricType": "cpu",
  "threshold": 85,
  "currentValue": 92.4,
  "alarmTime": "2026-05-07T14:32:00+09:00"
}
```

---

### 2. Collector (`src/collector/`)

#### 현재: Mock 수집기 (`mock_collector.py`)

알람 메트릭 유형에 따라 실제와 유사한 로그를 생성하여 반환.
NCP 키 및 API 경로 확인 후 실제 수집기로 교체 예정.

지원 메트릭 유형별 Mock 로그: CPU / Memory / Disk / Network / HTTP

#### 향후: NCP Log Analytics API (`ncp_collector.py`)

```
POST https://cloudloganalytics.apigw.ntruss.com/{path}
  startTime: alarm_time - 15min
  endTime:   alarm_time + 15min
  serverIp:  target_ip
```

> **현황**: NCP HMAC 서명 검증 완료, 정확한 API 경로 미확인.
> 참고: `docs/integration-notes.md`

#### 수집 실패 시 처리

로그 수집 실패해도 파이프라인 중단하지 않음 (graceful degradation).
알람 정보만으로 AI 분석 계속 진행.

---

### 3. Analyzer (`src/analyzer/`)

#### AI API: Timely GPT Native API

```
1. GET  /sdk-auth/authenticate  (X-Timely-API: {key}) → JWT
2. POST /llm-completion         (Bearer: {jwt})
   body: { session_id, messages, model, instructions,
           output_type: "JSON", output_schema, locale: "ko" }
3. response["parsed"]  →  구조화된 분석 결과 dict
```

Base URL: `https://hello.timelygpt.co.kr/api/v2/chat`  
모델: `gpt-5.1`  
JWT 유효시간: ~55분 (자동 갱신)

**Claude API 교체 시:** `src/analyzer/ai_client.py` 내부만 수정.

#### 분석 결과 포맷

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

#### 응답 시간

단순 채팅 ~3초, output_schema 분석 ~30~60초.  
웹훅은 202 즉시 반환 후 BackgroundTasks에서 처리하므로 문제 없음.

---

### 4. Responder — Phase 2

현재 미구현. 향후 Slack Incoming Webhook 연동 예정.

```
🚨 [CRITICAL] CPU 과부하 - web-01
📊 알람 정보 / 🤖 AI 분석 결과 / ⚡ 즉시 조치 / 🔗 상세 링크
```

---

### 5. DB 스키마 (`src/db/`)

#### incidents

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER PK | |
| alarm_id | TEXT UNIQUE | Cloud Insight 알람 ID |
| alarm_name | TEXT | 알람 이름 |
| resource_name | TEXT | 대상 서버/리소스 |
| metric_type | TEXT | CPU / Memory / Disk / Network / HTTP |
| threshold_value | TEXT | 임계값 |
| current_value | TEXT | 측정값 |
| alarm_time | DATETIME | 알람 발생 시각 |
| status | TEXT | `processing` / `analyzed` / `ai_failed` |
| severity | TEXT | Critical / High / Medium / Low |
| raw_alarm | JSON | 원본 페이로드 |
| created_at | DATETIME | |
| updated_at | DATETIME | |

#### incident_logs

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER PK | |
| incident_id | INTEGER FK | |
| source | TEXT | `mock` / `ncp_api` / `ssh` |
| log_content | TEXT | 로그 원문 |
| log_timestamp | DATETIME | |

#### analysis_results

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | INTEGER PK | |
| incident_id | INTEGER FK UNIQUE | |
| cause_category | TEXT | |
| cause_detail | TEXT | |
| severity | TEXT | |
| impact_scope | TEXT | |
| immediate_actions | JSON | list[str] |
| prevention | TEXT | |
| confidence | TEXT | 높음 / 보통 / 낮음 |
| raw_response | TEXT | AI 원본 응답 |
| created_at | DATETIME | |

---

## 배포 구성

```
중앙 서버 (NCP Server 또는 로컬)
  └── uvicorn src.main:app --host 0.0.0.0 --port 8000
        ├── POST /webhook/alarm  ← Cloud Insight가 호출
        ├── GET  /               ← 웹 대시보드
        └── GET  /incidents/{id} ← 장애 상세
```

외부에서 Webhook을 받으려면 공인 IP 또는 NCP Load Balancer 필요.  
개발/데모 환경에서는 ngrok으로 로컬 터널링 가능.
