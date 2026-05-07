# AI 에이전트 설계

## 에이전트 역할 정의

이 시스템의 AI는 단순 Q&A가 아닌 **구조화된 분석 에이전트**로 동작.
Timely GPT Native API의 `output_schema`를 활용해 응답 포맷을 강제하고 신뢰성을 높임.

> **현재 구현:** Timely GPT (`gpt-5.1`) — `src/analyzer/ai_client.py`  
> **교체 예정:** Claude API (`claude-sonnet-4-6`) — 해당 파일 내부만 수정

---

## 프롬프트 설계 (`src/analyzer/prompts.py`)

### Instructions (System Prompt 역할)

```
당신은 클라우드 인프라 전문 SRE(Site Reliability Engineer)입니다.
NCP(Naver Cloud Platform) 환경에서 발생하는 서버 장애를 분석하는 역할을 합니다.

분석 원칙:
1. 주어진 로그와 메트릭 데이터만을 근거로 판단하세요.
2. 불확실한 경우 confidence를 "낮음"으로 설정하세요.
3. immediate_actions는 구체적이고 실행 가능한 명령어 수준으로 작성하세요.
4. severity 판단은 서비스 영향도 기준으로 하세요.
```

### User Prompt 템플릿

```
## 장애 알람 정보
- 알람명: {alarm_name}
- 리소스: {resource_name}
- 메트릭: {metric_type} = {current_value} (임계값: {threshold_value})
- 발생시간: {alarm_time}

## 수집된 로그 (알람 시점 ±15분)
{log_content}

위 정보를 바탕으로 장애를 분석해주세요.
```

---

## output_schema 설계 (구조화 응답 강제)

Timely Native API의 `output_schema` 파라미터로 JSON 스키마를 직접 전달.
응답은 `response["parsed"]`에서 dict로 바로 추출 가능.

```python
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "cause_category": {
            "type": "string",
            "enum": [
                "리소스 부족",
                "애플리케이션 오류",
                "외부 서비스 장애",
                "인프라 이슈"
            ]
        },
        "cause_detail": {
            "type": "string",
            # 구체적인 원인 설명 (2-3문장)
        },
        "severity": {
            "type": "string",
            "enum": ["Critical", "High", "Medium", "Low"]
        },
        "impact_scope": {
            "type": "string"
            # 영향 받는 서비스 및 사용자 범위
        },
        "immediate_actions": {
            "type": "array",
            "items": {"type": "string"},
            # 즉시 실행 가능한 조치 목록 (명령어 포함)
        },
        "prevention": {
            "type": "string",
            # 재발 방지를 위한 중장기 권고사항
        },
        "confidence": {
            "type": "string",
            "enum": ["높음", "보통", "낮음"]
            # 로그 정보 충분도 기반 분석 신뢰도
        }
    },
    "required": [
        "cause_category", "cause_detail", "severity",
        "impact_scope", "immediate_actions", "prevention", "confidence"
    ]
}
```

---

## AI 클라이언트 구현 (`src/analyzer/ai_client.py`)

### 인증 흐름

```
1. GET  /sdk-auth/authenticate
   Header: X-Timely-API: {API_KEY}
   → { "data": { "access_token": "eyJ..." } }   (유효 ~55분)

2. POST /llm-completion
   Header: Authorization: Bearer {access_token}
   Body:   { session_id, messages, model, instructions,
             output_type, output_schema, locale, chat_type }
   → { "type": "final_response", "parsed": {...} }
```

### 핵심 구현

```python
import httpx
import time
from src.config import settings
from src.analyzer.prompts import INSTRUCTIONS, build_user_prompt, OUTPUT_SCHEMA

class TimelyAIClient:
    """
    Provider: Timely GPT Native API
    Claude API로 교체 시 이 클래스 내부만 수정.
    """
    _token: str | None = None
    _token_expires: float = 0

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires:
            return self._token
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.ai_base_url}/sdk-auth/authenticate",
                headers={"X-Timely-API": settings.ai_api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data["data"]["access_token"]
            self._token_expires = time.time() + 55 * 60
        return self._token

    async def analyze(self, alarm_data: dict, logs: list[str]) -> dict:
        token = await self._get_token()
        prompt = build_user_prompt(alarm_data, logs)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.ai_base_url}/llm-completion",
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                json={
                    "session_id": f"incident-{alarm_data.get('alarmId', 'unknown')}",
                    "messages": [{"role": "user", "content": prompt}],
                    "model": settings.ai_model,
                    "instructions": INSTRUCTIONS,
                    "output_type": "JSON",
                    "output_schema": OUTPUT_SCHEMA,
                    "chat_type": "DYNAMIC_CHAT",
                    "locale": "ko",
                },
                timeout=90,  # 분석 요청은 최대 60초 소요
            )
            resp.raise_for_status()
            return resp.json()["parsed"]

ai_client = TimelyAIClient()
```

---

## 로그 전처리 전략

```
전략 우선순위 (토큰 절약):
1. ERROR / CRITICAL / FATAL / OOM 레벨 로그 전체 포함
2. WARNING 레벨 로그 (알람 전후 5분)
3. 알람 시점 ±2분 전체 로그
4. 나머지는 샘플링 (10줄에 1줄)

목표 토큰: 6,000 이하 (로그 부분)
```

```python
def preprocess_logs(raw_logs: list[str], alarm_time: datetime) -> str:
    error_lines = [l for l in raw_logs if any(
        kw in l.upper() for kw in ["ERROR", "CRITICAL", "FATAL", "OOM", "KILLED"]
    )]
    near_alarm = get_logs_near_time(raw_logs, alarm_time, minutes=2)
    sampled = raw_logs[::10]

    combined = deduplicate(error_lines + near_alarm + sampled)
    return truncate_to_tokens(combined, max_tokens=6000)
```

---

## 에이전트 확장 계획 (Phase 2)

### 멀티턴 조사 에이전트

```
1회차: 초기 분석 → "DB 연결 로그 추가 확인 필요" 판단
2회차: DB 로그 수집 → 재분석
3회차: 최종 결론
```

Timely `session_id` + `checkpoint_id`로 대화 이어가기 지원 가능.

### 벡터 검색 연동 (유사 장애 참조)

```python
# 과거 장애 임베딩 저장
# 신규 장애 발생 시 유사 케이스 top-3 검색
# AI 프롬프트에 과거 해결 사례 추가 컨텍스트로 제공
```
