"""Test Timely GPT Native API (non-OpenAI-compat mode)."""
import json
import urllib.request
import urllib.error

KEY = "tgpt_sk_18f4cd3c098e58212bcc44f937999ed6d1486c5c4822a667f669525a05ea0ab8"
NATIVE_BASE = "https://hello.timelygpt.co.kr/api/v2/chat"

headers = {"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"}


def req(method, path, body=None):
    url = f"{NATIVE_BASE}{path}"
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]
    except Exception as e:
        return 0, str(e)


# 1. Check available models
print("[1] Models list")
code, body = req("GET", "/models")
print(f"  HTTP {code}: {json.dumps(body, ensure_ascii=False)[:400] if isinstance(body, (dict, list)) else body}")

# 2. Simple chat with gpt-5.1
print("\n[2] Basic chat (gpt-5.1)")
code, body = req("POST", "/completions", {
    "session_id": "test-001",
    "messages": [{"role": "user", "content": "두 글자로만 답해: 작동해?"}],
    "model": "gpt-5.1",
    "instructions": "Be concise.",
})
print(f"  HTTP {code}: {json.dumps(body, ensure_ascii=False)[:400] if isinstance(body, dict) else body}")

# 3. JSON schema output (the key feature)
print("\n[3] output_schema (structured JSON)")
SCHEMA = {
    "type": "object",
    "properties": {
        "cause_category": {"type": "string"},
        "severity": {"type": "string", "enum": ["Critical", "High", "Medium", "Low"]},
        "immediate_actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["cause_category", "severity", "immediate_actions"]
}
code, body = req("POST", "/completions", {
    "session_id": "test-002",
    "messages": [{"role": "user", "content": "CPU 95% 알람 분석해줘. 서버 server-prod-01, 임계값 80%."}],
    "model": "gpt-5.1",
    "instructions": "NCP 인프라 SRE로서 장애를 분석합니다.",
    "output_type": "JSON",
    "output_schema": SCHEMA,
})
print(f"  HTTP {code}: {json.dumps(body, ensure_ascii=False)[:600] if isinstance(body, dict) else body}")
