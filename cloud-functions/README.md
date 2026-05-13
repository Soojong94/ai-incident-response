# NCP Cloud Functions — 소스 템플릿

이 디렉토리는 NCP Cloud Functions 에 등록되는 두 액션의 **템플릿 소스**입니다.
실제 등록은 사이트 설정과 식별자가 치환된 코드를 운영 대시보드 가이드 페이지에서 복사해 NCP 콘솔에 붙여넣어 진행합니다.

## 구성

| 액션 | 파일 | 역할 |
|------|------|------|
| `cf_wms_poll` | [`cf_wms_poll/main.py`](cf_wms_poll/main.py) | Cron — WMS 시나리오 폴링 → errorCount 임계값 초과 시 CLA → OBS export |
| `cf_obs_to_webhook` | [`cf_obs_to_webhook/main.py`](cf_obs_to_webhook/main.py) | OBS Object Created 이벤트 → AI 서버 `/webhook/alarm` 호출 |

## 등록 가이드

운영 대시보드 → **📖 가이드** 메뉴 사용. 사이트별로 식별자(SCENARIO_ID, OBS_BUCKET, RESOURCE_NAME, WEBHOOK_URL)가 자동 치환된 코드와 액션/트리거 이름이 표시됩니다.

- **인프라 사전 준비**: `/guide/infra` — NCP 루트키, Log Agent, OBS, WMS 시나리오 등록까지
- **사이트별 CF 등록**: `/sites/{id}/guide` — 액션 2개 + 트리거 2개 등록 가이드

## 이 디렉토리는 언제 직접 수정하나

- CF 코드 자체의 로직 변경 (에러 처리 강화, 페이로드 필드 추가 등)
- 변경 후 운영 대시보드의 가이드 페이지가 자동으로 새 코드를 보여줌 (사이트별 식별자 치환은 그대로)

상수 자리 (예: `SCENARIO_ID = 0`, `OBS_BUCKET = "<your-bucket>"`)는 placeholder 입니다. 직접 편집하지 마세요 — 사이트 설정에 값을 등록하면 가이드 페이지가 알아서 치환합니다.
