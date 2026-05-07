"""Test Timely output_schema with longer timeout."""
import json
import urllib.request
import urllib.error

KEY = "tgpt_sk_18f4cd3c098e58212bcc44f937999ed6d1486c5c4822a667f669525a05ea0ab8"
BASE = "https://hello.timelygpt.co.kr/api/v2/chat"


def auth():
    req = urllib.request.Request(
        f"{BASE}/sdk-auth/authenticate",
        headers={"X-Timely-API": KEY}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["data"]["access_token"]


token = auth()
ah = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}

SCHEMA = {
    "type": "object",
    "properties": {
        "cause_category": {"type": "string"},
        "severity": {"type": "string"},
        "immediate_actions": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string"},
    },
    "required": ["cause_category", "severity", "immediate_actions", "confidence"]
}

print("Testing output_schema (timeout=90s)...")
req = urllib.request.Request(
    f"{BASE}/llm-completion",
    data=json.dumps({
        "session_id": "test-schema-01",
        "messages": [{"role": "user", "content": "CPU 95% 알람. 서버: server-prod-01. 임계값: 80%. 분석해줘."}],
        "model": "gpt-5.1",
        "instructions": "NCP SRE로서 간결하게 분석합니다.",
        "output_type": "JSON",
        "output_schema": SCHEMA,
        "chat_type": "DYNAMIC_CHAT",
    }).encode(),
    headers=ah
)
try:
    with urllib.request.urlopen(req, timeout=90) as r:
        body = json.loads(r.read())
        print(f"HTTP {r.status}")
        print(f"type: {body.get('type')}")
        print(f"message: {body.get('message')}")
        print(f"parsed: {json.dumps(body.get('parsed'), ensure_ascii=False, indent=2)}")
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode()[:400]}")
except Exception as e:
    print(f"ERROR: {e}")
