# API 통합 테스트 노트

> 테스트 일자: 2026-05-07  
> 환경: Windows 11, Python 3.x, stdlib only (urllib)

---

## 1. Timely GPT

### 개요

timelygpt.co.kr — 크레딧 기반 멀티모델 AI API 프록시 서비스.  
**두 가지 API 모드**가 존재하며 동작 방식이 완전히 다르다.

---

### 모드 A: OpenAI 호환 모드

| 항목 | 값 |
|------|----|
| Base URL | `https://hello.timelygpt.co.kr/api/v2/chat/bridge/openai` |
| 인증 | `Authorization: Bearer tgpt_sk_xxx` |
| SDK | Python `openai` 라이브러리 (`base_url`만 교체) |
| 문서 | `timely-chat/sdk/OPENAI_SDK_GUIDE.md` |

**사용 가능한 모델 (테스트 확인):**

| 모델 | 결과 | 비고 |
|------|------|------|
| `anthropic/claude-sonnet-4.6` | ❌ 404 | OpenRouter 데이터 정책 제한 |
| `anthropic/claude-haiku-4.5` | ✅ | 작동 |
| `openai/gpt-4o-mini` | ✅ | 작동 |
| `openai/gpt-4.1` | ❌ 404 | 데이터 정책 제한 |
| `openai/gpt-4o` | ❌ 404 | 데이터 정책 제한 |

> **주의**: 모델명에 점(`.`) 사용 — `claude-sonnet-4.6` (하이픈이 아님)  
> Claude Sonnet 계열 전체가 막혀 있음.  
> 해제 방법: https://openrouter.ai/settings/privacy 에서 데이터 정책 설정 변경

---

### 모드 B: Native Timely API ← **현재 채택**

| 항목 | 값 |
|------|----|
| Base URL | `https://hello.timelygpt.co.kr/api/v2/chat` |
| 인증 흐름 | API 키 → JWT 교환 → API 호출 (JWT 유효기간 ~55분) |
| 모델 | `gpt-5.1` |
| 구조화 출력 | `output_type: "JSON"` + `output_schema` (JSONSchema) |
| 응답 구조 | `body["parsed"]` (dict) 또는 `body["message"]` (JSON 문자열) |

**인증 흐름:**

```
1. GET /sdk-auth/authenticate
   Header: X-Timely-API: tgpt_sk_xxx
   → { "data": { "access_token": "eyJ..." } }

2. POST /llm-completion
   Header: Authorization: Bearer eyJ...
   Body: { session_id, messages, model, instructions, output_type, output_schema, ... }
   → { "type": "final_response", "message": "...", "parsed": {...} }
```

**테스트 결과:**

| 항목 | 결과 |
|------|------|
| JWT 인증 | ✅ HTTP 200 |
| 기본 채팅 (`gpt-5.1`) | ✅ HTTP 201 |
| `output_schema` 구조화 JSON | ✅ `parsed` 필드에 완벽 반환 |
| 한국어 응답 | `"locale": "ko"` 파라미터 추가 필요 (미추가 시 영어 응답) |

**응답 시간:** 단순 채팅 ~3초, `output_schema` 분석 요청 ~30~60초  
→ FastAPI `BackgroundTasks`로 처리 (웹훅 202 즉시 반환 후 백그라운드 실행)

**요청 Body 주요 파라미터:**

```python
{
    "session_id": "unique-per-incident",   # 필수
    "messages": [{"role": "user", "content": "..."}],
    "model": "gpt-5.1",
    "instructions": "시스템 프롬프트",
    "output_type": "JSON",                  # 구조화 출력
    "output_schema": { ... },               # JSONSchema 정의
    "chat_type": "DYNAMIC_CHAT",           # 기본값
    "locale": "ko",                        # 한국어 응답
}
```

**Claude API 교체 시 변경점:**  
`src/analyzer/ai_client.py` 내부만 수정. 응답 파싱 로직 (`parsed` → `choices[0].message.content`) 교체 필요.

---

## 2. NCP Cloud Insight

| 항목 | 값 |
|------|----|
| Base URL | `https://cw.apigw.ntruss.com` |
| Access Key | `ncp_iam_BPAMKR5Yp8saADhOAwrQ` |
| Secret Key | `ncp_iam_BPKMKRKI5mMOW7glW8OfVYbhJWXI2D8g79` |

**서명 생성 방식 (HMAC-SHA256):**

```python
import hmac, hashlib, base64, time

def ncp_headers(method: str, path: str) -> dict:
    ts = str(int(time.time() * 1000))
    msg = f"{method} {path}\n{ts}\n{NCP_ACCESS_KEY}"
    sig = base64.b64encode(
        hmac.new(NCP_SECRET_KEY.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "x-ncp-apigw-timestamp": ts,
        "x-ncp-iam-access-key": NCP_ACCESS_KEY,
        "x-ncp-apigw-signature-v2": sig,
        "Content-Type": "application/json",
    }
```

**테스트 결과:**

| 항목 | 결과 |
|------|------|
| 인증 서명 | ✅ HTTP 200 |
| SearchEvent | ✅ `POST /cw_fea/real/cw/api/event/search` |
| GetRuleGroupList | ✅ `POST /cw_fea/real/cw/api/rule/group/ruleGrp/query` |
| GetRuleGroup | ✅ `GET /cw_fea/real/cw/api/rule/group/ruleGrp/query/{prodKey}/{id}` |
| QueryData | ✅ `POST /cw_fea/real/cw/api/data/query` |
| GetProductSchema | ✅ `GET /cw_fea/real/cw/api/schema?prodName=...` |

**확인된 주요 엔드포인트:**

| API | Method | Path | 주요 파라미터 |
|-----|--------|------|--------------|
| SearchEvent | POST | `/cw_fea/real/cw/api/event/search` | `startTime`, `endTime` (Unix ms 문자열) |
| SearchEventById | POST | `/cw_fea/real/cw/api/event/searchById` | `eventId`, `ruleId` |
| QueryData | POST | `/cw_fea/real/cw/api/data/query` | `timeStart/End`, `cw_key`, `productName`, `metric`, `interval`, `aggregation`, `dimensions` |
| GetRuleGroupList | POST | `/cw_fea/real/cw/api/rule/group/ruleGrp/query` | `prodKey`, `pageSize`, `pageNum` |
| GetProductSchema | GET | `/cw_fea/real/cw/api/schema` | `prodName`, `cw_key` |

**SearchEvent 응답 필드 (events[]):**  
`eventId`, `eventLevel`(INFO/WARNING/CRITICAL), `ruleName`, `metric`, `detectValue`, `resourceName`, `prodName`, `regionCode`, `startTime/endTime`, `dimension.instanceNo`

**QueryData 응답:** `[[timestamp_ms, value], ...]` 형식의 시계열 배열

---

## 3. NCP Cloud Log Analytics (CLA)

| 항목 | 값 |
|------|----|
| Base URL | `https://cloudloganalytics.apigw.ntruss.com` |
| 인증 | Cloud Insight와 동일한 HMAC-SHA256 방식 |
| 문서 | https://api.ncloud-docs.com/docs/analytics-cloudloganalytics |

**확인된 주요 엔드포인트:**

| API | Method | Path | 설명 |
|-----|--------|------|------|
| SearchLogs | POST | `/api/{regionCode}-v1/logs/search` | 로그 조회 |
| GetLogStatus (total) | GET | `/api/{regionCode}-v1/logs/status/total` | 전체 로그 수 |
| GetLogStatus (period) | GET | `/api/{regionCode}-v1/logs/status/period` | 기간별 로그 수 |
| ExportLogs | POST | `/api/{regionCode}-v1/logs/export` | Object Storage로 내보내기 |

**regionCode:** `kr`(한국), `sgn`(싱가포르), `jpn`(일본), `uswn`(미국서부), `den`(독일)

**SearchLogs 요청 Body:**

```json
{
  "timestampFrom": "1746584400",
  "timestampTo":   "1746588000",
  "keyword":       "error",
  "logTypes":      "SYSLOG",
  "pageNo":        1,
  "pageSize":      100
}
```

**SearchLogs 응답:**

```json
{
  "code": 0,
  "message": "success",
  "result": {
    "totalCount": 42,
    "searchResult": [
      { "logTime": "...", "logType": "SYSLOG", "servername": "web-01", "logDetail": "..." }
    ]
  }
}
```

> **현황**: 인증 방식 확인 완료. 실제 로그 수집 테스트는 CLA 서비스 활성화 후 가능.  
> MVP에서는 Mock 수집기로 대체, `src/collector/ncp_collector.py`에 CLA 호출 코드 작성 완료.

---

## 4. 결론 및 개발 방향

```
AI 분석   : Timely Native API (gpt-5.1, output_schema)
           → 나중에 Claude API로 ai_client.py 내부만 교체
알람 수신  : NCP Cloud Insight Webhook (HMAC 검증)
로그 수집  : Mock (기본) → NCP CLA SearchLogs API (CLA 활성화 후 교체)
알림      : 웹 대시보드만 (Slack 추후)
```
