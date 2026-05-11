# Cloud Functions 데모 셋업 가이드

운영 데모 환경에서 WMS → CF#2 → CLA → OBS → CF#1 → AI 서버 흐름을 검증하기 위한 셋업 절차.

## 구성 요소

| 컴포넌트 | 위치 | 역할 |
|---------|------|------|
| nginx (test server) | `team1-test-server` | 모니터링 대상 — 정상/장애 응답 토글 |
| WMS 시나리오 9799 | NCP WMS | `team1-test-server` URL 모니터링, 1분 주기 |
| CF#2 `cf_wms_poll` | NCP Cloud Functions | 5분 cron 폴링, errorCount ≥ 1 시 CLA export |
| CLA SearchLogs Export | NCP Cloud Log Analytics | 5분치 로그를 OBS로 비동기 export |
| OBS 버킷 `team1-demo` | NCP Object Storage | CLA가 export한 JSONL 파일 저장 |
| CF#1 `cf_obs_to_webhook` | NCP Cloud Functions | OBS Object Created 이벤트 → webhook 호출 |
| AI 서버 | `https://tbit-msp.kro.kr` | OBS 다운로드 → AI 분석 → 이메일 발송 + 대시보드 |

## 전체 흐름

```
                                      [WMS scenario 9799]
                                              │
                                              │ (1분마다 URL 체크)
                                              ▼
                                       errorCount 누적
                                              │
                                              │ (5분 cron 폴링)
                                              ▼
                                       CF#2 cf_wms_poll
                                              │
                                  errorCount ≥ 1 ─▶ CLA Export API
                                                          │
                                                          ▼ (비동기, 수 초~수십 초)
                                                 OBS team1-demo/CLA-*
                                                          │
                                                          │ Object Created
                                                          ▼
                                             CF#1 cf_obs_to_webhook
                                                          │
                                                          ▼
                                     POST https://tbit-msp.kro.kr/webhook/alarm
                                                          │
                                                          ▼
                                                AI 서버 백그라운드 파이프라인:
                                                  ① OBS 다운로드
                                                  ② Timely GPT 분석
                                                  ③ DB 저장 + 이메일
```

## 1. nginx 토글 스크립트 배포 (test server)

`scripts/start_error.sh`, `scripts/stop_error.sh`를 test server에 복사 후 실행 권한 부여.

```bash
# test server (root)
chmod +x ~/start_error.sh ~/stop_error.sh
```

| 동작 | 명령 |
|------|------|
| 장애 시작 | `~/start_error.sh` |
| 정상 복구 | `~/stop_error.sh` |

## 2. WMS 시나리오 확인

NCP 콘솔 → WMS → 시나리오 ID `9799` (운영 환경 기준 — 환경 다르면 갱신).

- 모니터링 주기: 1분 (MIN1, 단 API 조회는 MIN5 집계 단위)
- 모니터링 URL: `team1-test-server` 의 nginx 엔드포인트
- 결과: ERROR / SUCCESS 카운트가 시나리오 결과 화면에 누적

## 3. NCP Cloud Functions 배포

`cloud-functions/README.md` 참고. 두 액션 + 두 트리거 등록.

### 액션

| 액션명 | 소스 | 런타임 |
|--------|------|--------|
| `team1-demo-pkg/team1-demo-check` (예시) | `cloud-functions/cf_wms_poll/main.py` | python:3.13 |
| `team1-demo-pkg/team1-demo-call-api` (예시) | `cloud-functions/cf_obs_to_webhook/main.py` | python:3.13 |

### 트리거

| 트리거명 | 타입 | 디폴트 파라미터 | 연결 액션 |
|---------|------|-----------------|-----------|
| `wms-polling` | Cron `*/5 * * * *` KST | NCP IAM 키 JSON | `cf_wms_poll` |
| `obs-team1-demo-created` | OBS Event (Object Created, `team1-demo`) | 비움 | `cf_obs_to_webhook` |

## 4. 동작 검증 시나리오

| 단계 | 행동 | 기대 결과 | 확인 위치 |
|------|------|-----------|-----------|
| 1 | test server에서 `start_error.sh` | nginx 500 응답 | `curl -i http://localhost/` |
| 2 | 1~5분 대기 | WMS 콘솔의 errorCount 증가 | NCP WMS 시나리오 결과 |
| 3 | cron 발화 (5분 경계) | `cf_wms_poll` 실행 로그에 `errorCount > 0` | CF 실행 이력 |
| 4 | CLA export 완료 | OBS `team1-demo`에 `CLA-YYYYMMDDHHMMSS` 폴더 도착 | OBS 콘솔 |
| 5 | OBS 이벤트 자동 발화 | `cf_obs_to_webhook` 실행 로그에 `webhook status: 202` | CF 실행 이력 |
| 6 | AI 분석 시작 | 대시보드에 새 incident `processing` → `analyzed` | `https://tbit-msp.kro.kr` |
| 7 | 분석 완료 | 알림 이메일 수신 | 받은편지함 |
| 8 | `stop_error.sh` | 흐름 정지, 정상 응답 복구 | |

## 5. 트러블슈팅 메모

지금까지 만난 문제들과 원인:

| 증상 | 원인 | 해결 |
|------|------|------|
| WMS API 응답이 모두 0 | `period_type='MIN1'` 최소값이 5라 빈 결과 | `MIN5`로 두고 30분 윈도우 |
| 그래도 0 | scenarioId가 `9789`였는데 실제는 `9799` | 콘솔에서 ID 재확인 |
| OBS는 도착하는데 AI 서버 incident 없음 | OBS Event 트리거 미등록 | `obs-team1-demo-created` 트리거 생성 + CF#1 연결 |
| webhook 401 (가능성) | `WEBHOOK_SECRET` 설정돼 있는데 CF가 서명 안 함 | AI 서버 `.env`의 `WEBHOOK_SECRET` 빈 값 유지 |

## 6. 관련 파일

| 위치 | 내용 |
|------|------|
| [cloud-functions/](../cloud-functions/) | CF#1 / CF#2 소스 + 배포 README |
| [scripts/start_error.sh](../scripts/start_error.sh) | nginx 500 모드 |
| [scripts/stop_error.sh](../scripts/stop_error.sh) | nginx 200 복구 |
| [docs/architecture.md](architecture.md) | AI 서버 본체 아키텍처 |
| [docs/integration-notes.md](integration-notes.md) | NCP / Timely API 통합 테스트 결과 |
| [src/webhook/handler.py](../src/webhook/handler.py) | webhook 수신부 (CF#1이 호출하는 곳) |
| [src/collector/obs_collector.py](../src/collector/obs_collector.py) | OBS 다운로드 로직 |
