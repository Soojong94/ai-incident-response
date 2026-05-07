"""Test Timely GPT Native API with correct auth flow."""
import json
import urllib.request
import urllib.error

KEY = "tgpt_sk_18f4cd3c098e58212bcc44f937999ed6d1486c5c4822a667f669525a05ea0ab8"
BASE = "https://hello.timelygpt.co.kr/api/v2/chat"


def raw_req(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:600]
    except Exception as e:
        return 0, str(e)


# Step 1: Authenticate → get JWT access token
print("[1] Authenticate")
code, body = raw_req("GET", f"{BASE}/sdk-auth/authenticate",
                     headers={"X-Timely-API": KEY})
print(f"  HTTP {code}: {json.dumps(body, ensure_ascii=False)[:300] if isinstance(body, dict) else body}")

if isinstance(body, dict) and body.get("success"):
    access_token = body["data"]["access_token"]
    print(f"  access_token: {access_token[:40]}...")
else:
    print("  Auth failed, stopping.")
    exit(1)

auth_headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {access_token}",
}


# Step 2: Simple chat with gpt-5.1
print("\n[2] Basic chat")
code, body = raw_req("POST", f"{BASE}/llm-completion", {
    "session_id": "test-001",
    "messages": [{"role": "user", "content": "두 글자로만 답해: 작동해?"}],
    "model": "gpt-5.1",
    "instructions": "Be concise.",
    "chat_type": "DYNAMIC_CHAT",
}, auth_headers)
print(f"  HTTP {code}: {json.dumps(body, ensure_ascii=False)[:300] if isinstance(body, dict) else body}")


# Step 3: JSON schema output
print("\n[3] output_schema - SRE analysis")
SCHEMA = {
    "type": "object",
    "properties": {
        "cause_category": {"type": "string", "enum": ["리소스 부족", "애플리케이션 오류", "외부 서비스 장애", "인프라 이슈"]},
        "cause_detail": {"type": "string"},
        "severity": {"type": "string", "enum": ["Critical", "High", "Medium", "Low"]},
        "impact_scope": {"type": "string"},
        "immediate_actions": {"type": "array", "items": {"type": "string"}},
        "prevention": {"type": "string"},
        "confidence": {"type": "string", "enum": ["높음", "보통", "낮음"]},
    },
    "required": ["cause_category", "cause_detail", "severity", "impact_scope", "immediate_actions", "prevention", "confidence"]
}
code, body = raw_req("POST", f"{BASE}/llm-completion", {
    "session_id": "test-002",
    "messages": [{"role": "user", "content": "CPU 95.3% 알람 분석. 서버: server-prod-01, 임계값 80%.\n로그:\n2025-01-15 14:28 ERROR GC overhead limit exceeded\n2025-01-15 14:30 CRITICAL CPU 94.8% - threads starving"}],
    "model": "gpt-5.1",
    "instructions": "당신은 NCP 인프라 시니어 SRE입니다. 클라우드 장애를 분석합니다.",
    "output_type": "JSON",
    "output_schema": SCHEMA,
    "chat_type": "DYNAMIC_CHAT",
}, auth_headers)
print(f"  HTTP {code}: {json.dumps(body, ensure_ascii=False)[:800] if isinstance(body, dict) else body}")
