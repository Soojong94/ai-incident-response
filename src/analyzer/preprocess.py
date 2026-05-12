"""
AI prompt 에 보낼 로그 줄을 우선순위별로 추리는 모듈.

작은 케이스(전체 ≤ max_chars)는 그대로 통과. 큰 케이스만 트리밍:
  1순위 — ERROR / CRITICAL / FATAL / OOM / KILLED 등 키워드 포함 줄
  2순위 — 알람 시점 ±5분 안의 줄
  3순위 — 그 외에서 1/5 샘플링

세 우선순위를 합치되 max_chars 초과 시 즉시 중단. AI 호출 전에만 사용 —
DB 의 incident_logs 와 UI 표시 로직은 영향 없음.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

ERROR_KEYWORDS = (
    "ERROR", "CRITICAL", "FATAL", "OOM", "KILLED",
    "PANIC", "EXCEPTION", "TRACEBACK", "SEGFAULT",
    "DEADLOCK", "REFUSED", "TIMEOUT", "UNREACHABLE",
)

# 줄 앞부분에서 ISO-ish 타임스탬프 추출용 — 다양한 포맷 허용
_TS_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")


def _parse_ts(line: str) -> datetime | None:
    m = _TS_RE.match(line)
    if not m:
        return None
    raw = m.group(1).replace(" ", "T").replace(",", ".").rstrip("Z")
    # +0900 → +09:00
    raw = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", raw)
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt.replace(tzinfo=None)  # naive 비교


def _parse_alarm_time(alarm_time: str | None) -> datetime | None:
    if not alarm_time:
        return None
    raw = alarm_time.replace("Z", "")
    raw = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", raw)
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=None)
    except ValueError:
        return None


def preprocess_logs(
    records: list[str],
    alarm_time: str | None = None,
    max_chars: int = 8000,
    window_minutes: int = 5,
    sample_every: int = 5,
) -> list[str]:
    """입력이 max_chars 이하면 그대로 반환. 초과 시 우선순위 보존 트리밍."""
    if not records:
        return []

    total = sum(len(l) + 1 for l in records)  # +1 for newline
    if total <= max_chars:
        return records

    logger.info(
        "preprocess_logs: 트리밍 적용 (입력 %d줄/%dB → 목표 ≤%dB)",
        len(records), total, max_chars,
    )

    # 우선순위 1: ERROR 키워드
    error_lines: list[str] = []
    error_set: set[int] = set()
    for i, line in enumerate(records):
        upper = line.upper()
        if any(kw in upper for kw in ERROR_KEYWORDS):
            error_lines.append(line)
            error_set.add(i)

    # 우선순위 2: 알람 시점 ±N분
    near_lines: list[str] = []
    near_set: set[int] = set()
    atime = _parse_alarm_time(alarm_time)
    if atime:
        lo = atime - timedelta(minutes=window_minutes)
        hi = atime + timedelta(minutes=window_minutes)
        for i, line in enumerate(records):
            if i in error_set:
                continue
            lt = _parse_ts(line)
            if lt and lo <= lt <= hi:
                near_lines.append(line)
                near_set.add(i)

    # 우선순위 3: 1/N 샘플링 (이미 채택된 줄 제외)
    sampled: list[str] = []
    for i, line in enumerate(records):
        if i % sample_every == 0 and i not in error_set and i not in near_set:
            sampled.append(line)

    # 합치고 cap (1 → 2 → 3 순서)
    out: list[str] = []
    used = 0
    for line in error_lines + near_lines + sampled:
        cost = len(line) + 1
        if used + cost > max_chars:
            break
        out.append(line)
        used += cost

    # 시간순으로 재정렬 (AI 가 시계열로 읽을 수 있게) — 원본 records 순서 유지
    order_map = {l: i for i, l in enumerate(records)}
    out.sort(key=lambda l: order_map.get(l, 1_000_000))

    logger.info(
        "preprocess_logs: 결과 %d줄/%dB (err=%d, near=%d, sample=%d 후보)",
        len(out), used, len(error_lines), len(near_lines), len(sampled),
    )
    return out
