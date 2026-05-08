"""
NCP Object Storage에서 로그 파일을 다운로드하는 수집기.
boto3 S3 호환 API 사용 (루트 계정 키 필요 — IAM 키는 소유자 불일치로 접근 불가).
"""
import json
import logging

import boto3

from src.config import settings

logger = logging.getLogger(__name__)

OBS_ENDPOINT = "https://kr.object.ncloudstorage.com"


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=OBS_ENDPOINT,
        aws_access_key_id=settings.ncp_access_key,
        aws_secret_access_key=settings.ncp_secret_key,
    )


async def collect(bucket: str, object_key: str) -> list[str]:
    """OBS에서 JSONL 로그 파일을 다운로드하고 줄 단위로 반환."""
    logger.info("OBS 로그 수집 시작: bucket=%s, key=%s", bucket, object_key)
    resp = _s3_client().get_object(Bucket=bucket, Key=object_key)
    body = resp["Body"].read().decode("utf-8", errors="replace")

    priority = []
    fallback = []
    for line in body.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            ts = record.get("@timestamp", "")
            log_type = record.get("type", "")
            message = record.get("message", line)
            entry = f"{ts} [{log_type}] {message}"
            if log_type in ("nginx_error", "nginx_access"):
                priority.append(entry)
            else:
                fallback.append(entry)
        except json.JSONDecodeError:
            fallback.append(line)

    logs = (priority + fallback)[:100]
    logger.info("OBS 로그 %d줄 수집 완료 (priority=%d)", len(logs), len(priority))
    return logs
