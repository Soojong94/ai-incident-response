"""사이트별 알람 임계값(CPU/Mem/Disk) → vmalert 룰(generated.yml) 생성.

대시보드에서 사이트 임계값을 저장할 때마다 이 파일을 다시 써서 vmalert가 핫리로드한다.
호스트 선택: 사이트에 패턴이 있으면 host=~<glob→regex>, 없으면 host="<이름>".
발화 라벨 host는 메트릭/by(host)에서 나오므로 Alertmanager → /webhook/alert 매칭에 그대로 쓰인다.
"""
import json
import logging
import os
import re

from src.config import settings
from src.db.models import Site

logger = logging.getLogger(__name__)

# 실제 디스크만 (가상 파일시스템 제외)
_FSTYPE = 'fstype!~"tmpfs|devtmpfs|overlay|squashfs|nfs|nfs4"'


def _matcher(site: Site) -> str:
    """사이트의 host PromQL 셀렉터."""
    if site.resource_pattern:
        rx = re.escape(site.resource_pattern).replace(r"\*", ".*").replace(r"\?", ".")
        return f"host=~{json.dumps(rx)}"
    return f"host={json.dumps(site.name)}"


def _rule(alert: str, expr: str, for_seconds: int, alarm: str, summary: str) -> str:
    return (
        f"      - alert: {alert}\n"
        f"        expr: |\n"
        f"          {expr}\n"
        f"        for: {for_seconds}s\n"
        f"        labels:\n"
        f"          alarm: {alarm}\n"
        f"        annotations:\n"
        f"          summary: {json.dumps(summary, ensure_ascii=False)}\n"
    )


def generate_rules_yaml(sites: list[Site]) -> str:
    out = [
        "# 자동 생성 — 사이트별 알람 임계값. 직접 수정 금지(대시보드에서 변경).\n",
        "groups:\n",
        "  - name: site-thresholds\n",
        "    interval: 30s\n",
        "    rules:\n",
    ]
    any_rule = False
    for s in sites:
        if not s.enabled or not getattr(s, "alarm_enabled", True):
            continue
        m = _matcher(s)
        fr = s.alarm_for_seconds or 300
        if s.cpu_threshold:
            # 1분 평균 CPU% — 지속시간(for=alarm_for_seconds) 동안 연속 초과해야 발화
            out.append(_rule(
                "HighCPUUsage",
                f'(1 - avg by (host) (rate(node_cpu_seconds_total{{mode="idle",{m}}}[1m]))) * 100 > {s.cpu_threshold}',
                fr, "cpu", "CPU 과부하: {{ $labels.host }} {{ $value | humanize }}%",
            ))
            any_rule = True
        if s.mem_threshold:
            out.append(_rule(
                "HighMemoryUsage",
                f'(1 - (node_memory_MemAvailable_bytes{{{m}}} / node_memory_MemTotal_bytes{{{m}}})) * 100 > {s.mem_threshold}',
                fr, "mem", "메모리 과부하: {{ $labels.host }} {{ $value | humanize }}%",
            ))
            any_rule = True
        if s.disk_threshold:
            out.append(_rule(
                "HighDiskUsage",
                f'(1 - (node_filesystem_avail_bytes{{{m},{_FSTYPE}}} / node_filesystem_size_bytes{{{m},{_FSTYPE}}})) * 100 > {s.disk_threshold}',
                fr, "disk", "디스크 부족: {{ $labels.host }} {{ $value | humanize }}%",
            ))
            any_rule = True
    if not any_rule:
        # 빈 그룹은 vmalert가 거부 → 절대 발화 안 하는 placeholder
        out.append("      - alert: _noop\n        expr: vector(0) > 1\n")
    return "".join(out)


def write_rules(db) -> bool:
    """현재 사이트들의 임계값으로 generated.yml 갱신. 실패해도 앱은 계속(graceful)."""
    try:
        sites = db.query(Site).all()
        content = generate_rules_yaml(sites)
        os.makedirs(settings.vmalert_rules_dir, exist_ok=True)
        path = os.path.join(settings.vmalert_rules_dir, "generated.yml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("vmalert 룰 생성: %s (사이트 %d)", path, len(sites))
        return True
    except Exception as e:
        logger.warning("vmalert 룰 생성 실패: %s", e)
        return False
