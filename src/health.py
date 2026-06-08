"""메타 모니터링 — 시스템(중앙 인프라) 상태 수집.

대시보드 '시스템 상태' 페이지용. 전부 best-effort + 타임아웃 + graceful degradation:
한 항목이 실패해도 나머지는 채워서 반환한다.
- 중앙 디스크: app 컨테이너에 마운트된 data 경로(=호스트 fs) 사용량
- 파이프라인 헬스: VM/VL/vmalert/Alertmanager live HTTP 헬스체크
- 데이터 크기: VM/VL self /metrics 에서 best-effort
- 에이전트 생존: VM에 last-seen 쿼리(node_uname_info staleness)
"""
import logging
import os
import shutil

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


def _http_ok(url: str, timeout: float = 3.0):
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(url)
        return (r.status_code < 400), f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)[:80]


def _metric_sum(metrics_url: str, metric_name: str, timeout: float = 3.0):
    """대상 서비스의 /metrics 를 직접 긁어 해당 메트릭 값들을 합산. 없으면 None."""
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(metrics_url)
        if r.status_code >= 400:
            return None
        total = 0.0
        found = False
        for line in r.text.splitlines():
            if line.startswith("#") or not line.startswith(metric_name):
                continue
            # "metric_name{...} 123" 또는 "metric_name 123"
            head = line.split("{")[0].split(" ")[0]
            if head != metric_name:
                continue
            try:
                total += float(line.rsplit(" ", 1)[1])
                found = True
            except (ValueError, IndexError):
                pass
        return total if found else None
    except Exception:
        return None


def _gb(n):
    return round(n / 1_000_000_000, 2) if n is not None else None


def _agent_liveness(vm_url: str, stale_seconds: int = 180, timeout: float = 4.0):
    """VM에 host별 마지막 수신 이후 경과초(staleness)를 질의. [{host, stale_seconds, alive}]."""
    out = []
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(vm_url + "/api/v1/query",
                      params={"query": "time() - timestamp(node_uname_info)"})
        data = r.json().get("data", {}).get("result", [])
        for item in data:
            host = (item.get("metric") or {}).get("host") or (item.get("metric") or {}).get("instance") or "?"
            try:
                secs = float(item.get("value", [0, "0"])[1])
            except (ValueError, IndexError, TypeError):
                secs = None
            out.append({
                "host": host,
                "stale_seconds": int(secs) if secs is not None else None,
                "alive": (secs is not None and secs <= stale_seconds),
            })
        out.sort(key=lambda x: (x["alive"], x["host"]))
    except Exception as e:
        logger.warning("agent liveness 조회 실패: %s", e)
    return out


def gather_system_status() -> dict:
    vm = settings.victoriametrics_url.rstrip("/")
    vl = settings.victorialogs_url.rstrip("/")
    vmalert = settings.vmalert_url.rstrip("/")
    am = settings.alertmanager_url.rstrip("/")

    # 1) 중앙 디스크 (app에 마운트된 data 경로 = 호스트 fs)
    disk = None
    try:
        path = "/app/data" if os.path.isdir("/app/data") else (
            "data" if os.path.isdir("data") else "/")
        t, u, f = shutil.disk_usage(path)
        disk = {
            "total_gb": _gb(t), "used_gb": _gb(u), "free_gb": _gb(f),
            "pct": round(u / t * 100, 1) if t else None,
            "path": path,
        }
    except Exception as e:
        logger.warning("disk 조회 실패: %s", e)

    # 2) 파이프라인 헬스 (live)
    checks = []
    for name, url in [
        ("VictoriaMetrics", vm + "/health"),
        ("VictoriaLogs", vl + "/health"),
        ("vmalert", vmalert + "/health"),
        ("Alertmanager", am + "/-/healthy"),
    ]:
        ok, info = _http_ok(url)
        checks.append({"name": name, "ok": ok, "info": info})

    # 3) 데이터 크기 (best-effort, self /metrics)
    storage = {
        "vm_bytes": _metric_sum(vm + "/metrics", "vm_data_size_bytes"),
        "vl_bytes": _metric_sum(vl + "/metrics", "vl_data_size_bytes"),
    }
    storage["vm_gb"] = _gb(storage["vm_bytes"])
    storage["vl_gb"] = _gb(storage["vl_bytes"])

    # 4) 에이전트 생존
    agents = _agent_liveness(vm, stale_seconds=settings.deadman_seconds)

    return {
        "disk": disk,
        "checks": checks,
        "pipeline_ok": all(c["ok"] for c in checks),
        "storage": storage,
        "agents": agents,
        "agents_total": len(agents),
        "agents_down": sum(1 for a in agents if not a["alive"]),
    }
