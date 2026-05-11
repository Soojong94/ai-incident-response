"""
CF#1 — OBS Object Created 이벤트 → AI 서버 webhook 호출

OBS 버킷 team1-demo에 CLA가 export한 로그 파일이 업로드되면
이 액션이 발화되어 AI 서버의 /webhook/alarm 엔드포인트를 호출.

트리거 종류: Object Storage Event (Object Created)
디폴트 파라미터: 비움 (OBS 이벤트가 container_name/object_name 자동 전달)
"""
import datetime
import json
import random
import time

import requests


WEBHOOK_URL = "https://tbit-msp.kro.kr/webhook/alarm"
RESOURCE_NAME = "team1-test-server"


def main(args):
    bucket_name = args.get("container_name")
    object_name = args.get("object_name")

    print("버킷이름:", bucket_name)
    print("파일이름:", object_name)

    alarm_time = datetime.datetime.fromtimestamp(time.time()).strftime("%Y-%m-%dT%H:%M:%S")
    random_number = random.randint(1000, 9999)
    alarm_id = f"{RESOURCE_NAME}-{random_number}"

    payload = {
        "alarmName": alarm_id,
        "alarmId": alarm_id,
        "resourceName": RESOURCE_NAME,
        "metricType": "url_error",
        "alarmTime": alarm_time,
        "obs_bucket": bucket_name,
        "obs_object_key": object_name,
    }

    response = requests.post(
        WEBHOOK_URL,
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
    )
    print("webhook status:", response.status_code)

    return {
        "result": "success",
        "bucket_name": bucket_name,
        "object_name": object_name,
        "webhook_status": response.status_code,
    }
