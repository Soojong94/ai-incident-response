# NCP Cloud Functions

이 디렉토리는 NCP Cloud Functions(VPC) 에 등록되는 두 액션의 소스 코드입니다.
AI 서버 본체(`src/`)와는 별도로 NCP 콘솔에서 배포됩니다.

## 구성

| 액션 | 트리거 | 역할 |
|------|--------|------|
| [cf_wms_poll/main.py](cf_wms_poll/main.py) | Cron `*/5 * * * *` | WMS 시나리오 폴링 → errorCount ≥ 1 시 CLA → OBS export |
| [cf_obs_to_webhook/main.py](cf_obs_to_webhook/main.py) | Object Storage Event (Object Created, `team1-demo`) | OBS 업로드 감지 → AI 서버 `/webhook/alarm` 호출 |

## 전체 흐름

```
[WMS scenario 9799]
       │ (cron 5분)
       ▼
   cf_wms_poll ── errorCount ≥ 1 ─▶ CLA SearchLogs Export ──▶ OBS:team1-demo
                                                                    │
                                                       Object Created 이벤트
                                                                    ▼
                                                       cf_obs_to_webhook
                                                                    │
                                       POST https://tbit-msp.kro.kr/webhook/alarm
                                                                    ▼
                                           AI 서버: OBS 다운로드 → AI 분석 → 메일
```

## 배포 방법

### 액션 등록 (둘 다 동일 방식)

1. NCP 콘솔 → Cloud Functions → 액션 → 액션 생성
2. 런타임: `python:3.13`
3. 코드: 해당 `main.py` 내용 전체 붙여넣기
4. 진입점(main function): `main`

### 트리거 등록

#### `cf_wms_poll` — Cron 트리거

| 필드 | 값 |
|------|---|
| 이름 | `wms-polling` |
| 타입 | Cron |
| 타임존 | UTC +09:00 |
| 표현식 | `*/5 * * * *` (5분마다) |
| 디폴트 파라미터 | NCP IAM 키 JSON (아래) |

```json
{
  "access_key": "YOUR_NCP_ACCESS_KEY",
  "secret_key": "YOUR_NCP_SECRET_KEY"
}
```

→ 생성 후 `cf_wms_poll` 액션과 연결.

#### `cf_obs_to_webhook` — OBS Event 트리거

| 필드 | 값 |
|------|---|
| 이름 | `obs-team1-demo-created` |
| 타입 | Object Storage Event |
| 버킷 | `team1-demo` |
| 이벤트 종류 | Object Created |
| Prefix / Suffix | 비움 |
| 디폴트 파라미터 | 비움 (이벤트가 `container_name`/`object_name` 자동 전달) |

→ 생성 후 `cf_obs_to_webhook` 액션과 연결.

## 동작 검증

| 순서 | 행동 | 기대 |
|------|------|------|
| 1 | 운영 test server에서 `scripts/start_error.sh` 실행 | nginx 500 응답 시작 |
| 2 | 5분 내 WMS 시나리오 9799가 ERROR 누적 | 콘솔에서 errorCount 증가 확인 |
| 3 | cron 발화 → `cf_wms_poll` 실행 | 액션 로그 `errorCount > 0`, OBS에 `CLA-*` 폴더 도착 |
| 4 | OBS 이벤트 → `cf_obs_to_webhook` 발화 | webhook status 202 |
| 5 | AI 서버 대시보드 (`https://tbit-msp.kro.kr`) | 새 incident → AI 분석 → 메일 |
| 6 | `scripts/stop_error.sh` 실행 | nginx 200 복구 |

## 알려진 한계

| 항목 | 설명 |
|------|------|
| 중복 export 가능 | `cf_wms_poll`의 조회 윈도우(30분)와 cron 주기(5분) 차이로 같은 5분 버킷 데이터가 여러 번 export될 수 있음. `ERROR_THRESHOLD`를 3 이상으로 올리거나 windowing 로직 강화 필요 |
| `alarmId` 충돌 가능성 | `cf_obs_to_webhook`이 `resourceName-NNNN` 랜덤 4자리로 생성 — 매우 낮은 확률로 충돌 시 AI 서버의 UNIQUE 제약으로 인서트 실패 |
| `metricType: "url_error"` | AI 서버의 mock_collector fallback 사전엔 없는 키. OBS 경로가 정상이면 mock을 안 타서 무관 |
