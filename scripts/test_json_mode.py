"""Test JSON mode + SRE analysis prompt with gpt-4o-mini."""
import json
import urllib.request
import urllib.error

KEY = "tgpt_sk_18f4cd3c098e58212bcc44f937999ed6d1486c5c4822a667f669525a05ea0ab8"
BASE = "https://hello.timelygpt.co.kr/api/v2/chat/bridge/openai"

SYSTEM = "You are a senior SRE analyzing cloud infrastructure incidents. Always respond in JSON."

USER = """Analyze this incident and respond with this exact JSON structure:
{
  "cause_category": "리소스 부족 | 애플리케이션 오류 | 외부 서비스 장애 | 인프라 이슈",
  "cause_detail": "2-3 sentence explanation",
  "severity": "Critical | High | Medium | Low",
  "impact_scope": "affected scope",
  "immediate_actions": ["action1", "action2"],
  "prevention": "prevention recommendation",
  "confidence": "높음 | 보통 | 낮음"
}

Incident:
- Alarm: server-prod-01 CPU usage threshold exceeded
- Metric: cpu_usage = 95.3% (threshold: 80%)
- Time: 2025-01-15 14:30:00

Logs:
2025-01-15 14:28:00 ERROR [app] GC overhead limit exceeded
2025-01-15 14:29:30 CRITICAL [system] CPU: 94.8% - threads starving
2025-01-15 14:30:00 ERROR [app] Request queue depth: 847"""

payload = json.dumps({
    "model": "openai/gpt-4o-mini",
    "messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": USER},
    ],
    "response_format": {"type": "json_object"},
    "max_tokens": 500,
    "temperature": 0.1,
}).encode()

req = urllib.request.Request(
    f"{BASE}/chat/completions", data=payload,
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"}
)

try:
    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read())
        content = result["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        print("SUCCESS - JSON response:")
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
        print(f"\nTokens: {result.get('usage', {})}")
except urllib.error.HTTPError as e:
    print(f"FAIL HTTP {e.code}: {e.read().decode()[:400]}")
except json.JSONDecodeError as e:
    print(f"JSON parse error: {e}\nRaw: {content[:300]}")
except Exception as e:
    print(f"ERROR: {e}")
