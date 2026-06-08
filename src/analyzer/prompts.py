from datetime import datetime

INSTRUCTIONS = """당신은 인프라 운영 전문 SRE(Site Reliability Engineer)입니다.
멀티클라우드/온프레미스를 가리지 않고, 리눅스·윈도우 서버의 장애를 로그·메트릭 기반으로 분석합니다.

분석 원칙:
1. 주어진 로그와 메트릭 데이터만을 근거로 판단하세요. **특정 클라우드 벤더(NCP/AWS/Azure/GCP 등)를 가정하거나 그 벤더 전용 서비스(예: Cloud Monitoring, CloudWatch)를 권하지 마세요.**
2. 불확실한 경우 confidence를 "낮음"으로 설정하세요.
3. immediate_actions는 구체적이고 실행 가능한 일반 명령어 수준(top, ps, df -h, free -m, journalctl, systemctl, iostat 등)으로 작성하세요.
4. prevention 권고도 **벤더 중립적인 일반 운영 관점**(리소스 증설, logrotate, 헬스체크/오토힐링, 모니터링 임계값 조정, 프로세스 점검 등)으로 작성하세요.
5. severity 판단은 서비스 영향도 기준으로 하세요.
6. **주어진 로그·메트릭·컨텍스트에 없는 사실을 지어내지 마세요.** 특히 (a) 과거 장애 이력은 아래 '과거 분석' 목록에 실제로 있는 것만 인용하고, 없으면 과거 이력을 언급하지 마세요. (b) 로그에 등장하지 않은 **서버명·IP·호스트·날짜**를 추측하거나 만들어내지 마세요. 확실치 않으면 단정하지 말고 confidence를 낮추세요."""

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
    past_analyses: list[dict] | None = None,
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

    past_section = ""
    if past_analyses:
        blocks = []
        for p in past_analyses:
            blocks.append(
                f"- #{p['incident_id']} ({p['alarm_time']}) {p['alarm_name']} / {p['severity']} / {p['cause_category']}\n"
                f"  요약: {p['cause_detail']}\n"
                f"  당시 권고: {p['prevention']}"
            )
        past_section = f"""## 참고: 같은 리소스의 과거 분석 (최신 → 과거 순)
{chr(10).join(blocks)}

위 정보는 참고용입니다. 현재 로그가 다른 원인을 가리키면 그에 따라 자유롭게 분석하세요.
명확하게 같은 패턴이 다시 보일 때만 과거 incident 번호를 짧게 언급해도 좋습니다.

"""

    return f"""## 장애 알람 정보
- 알람명: {_get(alarm_data, 'alarmName', 'alarm_name')}
- 리소스: {_get(alarm_data, 'resourceName', 'resource_name')}
- 메트릭: {_get(alarm_data, 'metricType', 'metric_type')} = {_get(alarm_data, 'currentValue', 'current_value')} (임계값: {_get(alarm_data, 'threshold', 'threshold_value')})
- 발생시간: {alarm_time}

{arch_section}{notes_section}{past_section}## 수집된 로그 (알람 시점 ±15분)
{log_section}

위 정보를 바탕으로 장애를 분석해주세요. 인프라 아키텍처와 과거 메모/분석이 제공된 경우, 컴포넌트 간 의존성과 반복 패턴을 함께 고려해 원인 후보를 좁히세요."""
