import json
import urllib.request
import urllib.error

KEY = "tgpt_sk_18f4cd3c098e58212bcc44f937999ed6d1486c5c4822a667f669525a05ea0ab8"
BASE = "https://hello.timelygpt.co.kr/api/v2/chat/bridge/openai"

for model in ["openai/gpt-4.1", "openai/gpt-4.1-mini", "openai/gpt-4o", "openai/gpt-4o-mini"]:
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Say OK"}],
        "max_tokens": 5
    }).encode()
    req = urllib.request.Request(
        f"{BASE}/chat/completions", data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
            msg = d["choices"][0]["message"]["content"]
            actual = d.get("model", "?")
            print(f"OK   {model} -> '{msg}' (actual: {actual})")
    except urllib.error.HTTPError as e:
        print(f"FAIL {model} HTTP {e.code}: {e.read().decode()[:120]}")
    except Exception as e:
        print(f"ERR  {model}: {e}")
