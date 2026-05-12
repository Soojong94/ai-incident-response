from datetime import datetime

INSTRUCTIONS = """당신은 클라우드 인프라 전문 SRE(Site Reliability Engineer)입니다.
NCP(Naver Cloud Platform) 환경에서 발생하는 서버 장애를 분석하는 역할을 합니다.

분석 원칙:
1. 주어진 로그와 메트릭 데이터만을 근거로 판단하세요.
2. 불확실한 경우 confidence를 "낮음"으로 설정하세요.
3. immediate_actions는 구체적이고 실행 가능한 명령어 수준으로 작성하세요.
4. severity 판단은 서비스 영향도 기준으로 하세요."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "cause_category": {
            "type": "string",
            "enum": ["리소스 부족", "애플리케이션 오류", "외부 서비스 장애", "인프라 이슈"],
        },
        "cause_detail": {"type": "string"},
        "severity": {
            "type": "string",
            "enum": ["Critical", "High", "Medium", "Low"],
        },
        "impact_scope": {"type": "string"},
        "immediate_actions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "prevention": {"type": "string"},
        "confidence": {
            "type": "string",
            "enum": ["높음", "보통", "낮음"],
        },
    },
    "required": [
        "cause_category", "cause_detail", "severity",
        "impact_scope", "immediate_actions", "prevention", "confidence",
    ],
}


def _get(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return "N/A"


def build_user_prompt(
    alarm_data: dict,
    logs: list[str],
    site_architecture: str | None = None,
    site_notes: list[str] | None = None,
) -> str:
    alarm_time = _get(alarm_data, "alarmTime", "alarm_time") or datetime.now().isoformat()
    log_section = "\n".join(logs) if logs else "(로그 수집 실패 — 알람 정보만으로 분석)"

    arch_section = ""
    if site_architecture and site_architecture.strip():
        arch_text = site_architecture.strip()[:2000]
        arch_section = f"""## 사이트 인프라 아키텍처
{arch_text}

"""

    notes_section = ""
    if site_notes:
        # 토큰 절약 — 최대 8건, 노트당 250자
        bullet_lines = [n[:250] for n in site_notes[:8]]
        if bullet_lines:
            notes_section = f"""## 이 사이트의 과거 분석/메모 (학습된 컨텍스트)
{chr(10).join(bullet_lines)}

위 메모 중 '★ pinned'가 붙은 항목은 이 사이트에서 반복 확인된 문제이거나 사람이 검증한 패턴입니다.
유사한 증상이 보이면 그 메모와의 연관성을 먼저 고려하세요.

"""

    return f"""## 장애 알람 정보
- 알람명: {_get(alarm_data, 'alarmName', 'alarm_name')}
- 리소스: {_get(alarm_data, 'resourceName', 'resource_name')}
- 메트릭: {_get(alarm_data, 'metricType', 'metric_type')} = {_get(alarm_data, 'currentValue', 'current_value')} (임계값: {_get(alarm_data, 'threshold', 'threshold_value')})
- 발생시간: {alarm_time}

{arch_section}{notes_section}## 수집된 로그 (알람 시점 ±15분)
{log_section}

위 정보를 바탕으로 장애를 분석해주세요. 인프라 아키텍처와 과거 메모가 제공된 경우, 컴포넌트 간 의존성과 반복 패턴을 함께 고려해 원인 후보를 좁히세요."""
