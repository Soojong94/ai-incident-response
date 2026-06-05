"""VictoriaMetrics에서 최근 활성 host 목록을 조회 — 사이트 자동 등록용.

분석/알람이 없어도, Alloy가 메트릭을 보내기 시작한 서버를 사이트 관리에 바로 띄우기 위해
최근 N분 안에 데이터가 있는 host 라벨 값만 가져온다. (3개월 보관 전체가 아니라 '현재 활성'만)
"""
import logging
import time

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


def list_active_hosts(minutes: int = 10) -> list[str]:
    """최근 N분 안에 메트릭을 보낸 host 라벨 값 목록. 실패 시 빈 리스트(graceful)."""
    end = int(time.time())
    start = end - max(1, minutes) * 60
    url = settings.victoriametrics_url.rstrip("/") + "/api/v1/label/host/values"
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, params={"start": start, "end": end})
            resp.raise_for_status()
            data = resp.json()
        if data.get("status") == "success":
            return [h for h in data.get("data", []) if h]
    except Exception as e:
        logger.warning("VictoriaMetrics host 목록 조회 실패: %s", e)
    return []
