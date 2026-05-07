# AI 에이전트 설계

## 에이전트 역할 정의

이 시스템의 AI는 단순 Q&A가 아닌 **구조화된 분석 에이전트**로 동작.
Claude의 Tool Use 기능을 활용해 응답 포맷을 강제하고 신뢰성을 높임.

---

## 프롬프트 설계

### System Prompt

```
당신은 클라우드 인프라 전문 SRE(Site Reliability Engineer)입니다.
NCP(Naver Cloud Platform) 환경에서 발생하는 서버 장애를 분석하는 역할을 합니다.

분석 원칙:
1. 주어진 로그와 메트릭 데이터만을 근거로 판단하세요.
2. 불확실한 경우 "추가 확인 필요"로 명시하세요.
3. 조치 권고는 구체적이고 실행 가능한 명령어 수준으로 작성하세요.
4. 심각도 판단은 서비스 영향도 기준으로 하세요.
```

### User Prompt 템플릿

```
## 알람 정보
- 서버명: {server_name} ({server_ip})
- 알람 항목: {metric} = {current_value} (임계값: {threshold})
- 발생 시각: {occurred_at}

## 최근 메트릭 (30분, 5분 평균)
{metrics_table}

## 수집된 로그 ({log_start} ~ {log_end})
{log_content}

위 정보를 바탕으로 장애를 분석해주세요.
```

---

## Tool Use 설계 (구조화 응답 강제)

### 분석 도구 정의

```python
tools = [
    {
        "name": "report_incident_analysis",
        "description": "장애 분석 결과를 구조화된 형태로 보고합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cause_category": {
                    "type": "string",
                    "enum": [
                        "리소스 부족",
                        "애플리케이션 오류",
                        "외부 서비스 장애",
                        "인프라 이슈",
                        "불명확 (추가 조사 필요)"
                    ],
                    "description": "장애 원인 대분류"
                },
                "cause_detail": {
                    "type": "string",
                    "description": "구체적인 원인 설명 (2-3문장)"
                },
                "severity": {
                    "type": "string",
                    "enum": ["Critical", "High", "Medium", "Low"],
                    "description": "서비스 영향도 기반 심각도"
                },
                "impact_scope": {
                    "type": "string",
                    "description": "영향 받는 서비스 및 사용자 범위"
                },
                "immediate_actions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 5,
                    "description": "즉시 실행 가능한 조치 목록 (명령어 포함)"
                },
                "prevention": {
                    "type": "string",
                    "description": "재발 방지를 위한 중장기 권고사항"
                },
                "confidence": {
                    "type": "string",
                    "enum": ["높음", "보통", "낮음"],
                    "description": "로그 정보 충분도 기반 분석 신뢰도"
                }
            },
            "required": [
                "cause_category",
                "cause_detail",
                "severity",
                "impact_scope",
                "immediate_actions",
                "prevention",
                "confidence"
            ]
        }
    }
]
```

---

## Claude 클라이언트 구현 (`src/analyzer/claude_client.py`)

```python
import anthropic
from .prompts import build_system_prompt, build_user_prompt

client = anthropic.Anthropic()

async def analyze_incident(incident_data: dict) -> dict:
    system = build_system_prompt()
    user = build_user_prompt(incident_data)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=ANALYSIS_TOOLS,
        tool_choice={"type": "any"}  # tool use 강제
    )

    # tool_use 블록에서 결과 추출
    for block in response.content:
        if block.type == "tool_use" and block.name == "report_incident_analysis":
            return block.input

    raise ValueError("AI가 분석 결과를 반환하지 않았습니다.")
```

---

## 로그 전처리 전략

### 토큰 절약을 위한 로그 슬라이싱

```
전략 우선순위:
1. ERROR / CRITICAL / FATAL 레벨 로그 전체 포함
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
    sampled = raw_logs[::10]  # 나머지 샘플링

    combined = deduplicate(error_lines + near_alarm + sampled)
    return truncate_to_tokens(combined, max_tokens=6000)
```

---

## 에이전트 확장 계획 (Phase 2)

### 멀티턴 조사 에이전트

단순 1회 분석에서 확장하여, 초기 분석 후 추가 조사가 필요한 경우
에이전트가 스스로 추가 로그를 요청하는 구조:

```
1회차: 초기 분석 → "DB 연결 로그 추가 확인 필요" 판단
2회차: DB 로그 수집 → 재분석
3회차: 최종 결론
```

### 벡터 검색 연동 (유사 장애 참조)

```python
# 과거 장애 임베딩 저장
# 신규 장애 발생 시 유사 케이스 top-3 검색
# Claude 프롬프트에 과거 해결 사례 추가 컨텍스트로 제공
```
