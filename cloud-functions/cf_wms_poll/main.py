"""
CF#2 — Cron 트리거로 WMS 시나리오 폴링 → errorCount >= 1 시 CLA export

5분 cron으로 WMS 시나리오의 최근 30분 결과를 조회.
errorCount가 1 이상이면 CLA SearchLogs Export API를 호출해
team1-demo OBS 버킷으로 최근 5분치 로그를 내보냄.

트리거 종류: Cron (예: */5 * * * * KST)
디폴트 파라미터:
    {
        "access_key": "YOUR_NCP_ACCESS_KEY",
        "secret_key": "YOUR_NCP_SECRET_KEY"
    }
"""
import base64
import hashlib
import hmac
import json
import time

import requests


# ── placeholder — 운영 대시보드 가이드(/sites/{id}/guide)가 자동 치환 ──
SCENARIO_ID = 0
OBS_BUCKET = "<your-bucket>"
CLA_REGION = "kr"
ERROR_THRESHOLD = 3   # 운영 기본. 테스트 사이트는 1로 낮춰서 사용
CLA_LOG_TYPES = "SYSLOG,nginx_access,nginx_error,security_log"


def main(args):
    access_key = args["access_key"]
    secret_key = args["secret_key"]

    result = check_wms(access_key, secret_key, SCENARIO_ID)
    error_count = result.get("errorCount", 0)

    if error_count >= ERROR_THRESHOLD:
        export_cla(access_key, secret_key, CLA_REGION, OBS_BUCKET)

    return result


def check_wms(acc, sec, scenario_id):
    """WMS 시나리오 결과 조회 — 최근 30분 윈도우, MIN5 집계 버킷."""
    method = "GET"
    api_url = "https://wms.apigw.ntruss.com"
    from_time = int(time.time() * 1000) - 1_800_000
    to_time = int(time.time() * 1000)
    action = f"/api/v1/scenarios/{scenario_id}/results?from={from_time}&to={to_time}&type=MIN5"

    sig, ts = make_signature(acc, sec, method, action)
    headers = {
        "x-ncp-apigw-signature-v2": sig,
        "x-ncp-apigw-timestamp": ts,
        "x-ncp-iam-access-key": acc,
        "Content-Type": "application/json",
    }

    response = requests.get(api_url + action, headers=headers)
    print("status:", response.status_code)
    print("body:", response.text)
    return json.loads(response.text)


def export_cla(acc, sec, region_code, bucket_name):
    """CLA SearchLogs Export → OBS 버킷으로 최근 5분 로그 내보내기."""
    method = "POST"
    api_url = "https://cloudloganalytics.apigw.ntruss.com"
    action = f"/api/{region_code}-v1/logs/search/export"

    sig, ts = make_signature(acc, sec, method, action)
    headers = {
        "x-ncp-apigw-signature-v2": sig,
        "x-ncp-apigw-timestamp": ts,
        "x-ncp-iam-access-key": acc,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "bucketname": bucket_name,
        "timestampFrom": int(time.time() * 1000) - 300_000,
        "timestampTo": int(time.time() * 1000),
        "logTypes": CLA_LOG_TYPES,
    }

    response = requests.post(api_url + action, headers=headers, json=payload)
    return json.loads(response.text)


def make_signature(acc, sec, method, message):
    """NCP API Gateway HMAC-SHA256 서명 생성."""
    timestamp = str(int(time.time() * 1000))
    secret_bytes = bytes(sec, "UTF-8")
    string_to_sign = bytes(f"{method} {message}\n{timestamp}\n{acc}", "UTF-8")
    signing_key = base64.b64encode(
        hmac.new(secret_bytes, string_to_sign, digestmod=hashlib.sha256).digest()
    )
    return signing_key, timestamp
