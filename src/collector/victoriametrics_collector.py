"""VictoriaMetrics에서 최근 활성 host 목록을 조회 — 사이트 자동 등록용.

분석/알람이 없어도, Alloy가 메트릭을 보내기 시작한 서버를 사이트 관리에 바로 띄우기 위해
최근 N분 안에 데이터가 있는 host 라벨 값만 가져온다. (3개월 보관 전체가 아니라 '현재 활성'만)
"""
import logging

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


def list_active_host_groups() -> list[tuple[str, str | None]]:
    """현재 활성(최근 ~5분 staleness) host와 그 group 라벨 목록 — (host, group).

    group 라벨은 게이트웨이가 자기+중계 트래픽에 찍는 상위 그룹(공인IP 단위).
    node_uname_info 인스턴트 쿼리로 host당 1줄. 실패 시 빈 리스트(graceful)."""
    url = settings.victoriametrics_url.rstrip("/") + "/api/v1/query"
    out: list[tuple[str, str | None]] = []
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, params={"query": "node_uname_info"})
            resp.raise_for_status()
            data = resp.json()
        if data.get("status") == "success":
            seen = set()
            for s in data.get("data", {}).get("result", []):
                m = s.get("metric", {})
                host = m.get("host")
                if not host or host in seen:
                    continue
                seen.add(host)
                out.append((host, (m.get("group") or None)))
    except Exception as e:
        logger.warning("VictoriaMetrics host/group 조회 실패: %s", e)
    return out
