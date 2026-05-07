import json
import urllib.request
import urllib.error
import base64
import hashlib
import hmac
import time

TIMELY_KEY = "tgpt_sk_18f4cd3c098e58212bcc44f937999ed6d1486c5c4822a667f669525a05ea0ab8"
BASE = "https://hello.timelygpt.co.kr/api/v2/chat/bridge/openai"

NCP_ACCESS_KEY = "ncp_iam_BPAMKR5Yp8saADhOAwrQ"
NCP_SECRET_KEY = "ncp_iam_BPKMKRKI5mMOW7glW8OfVYbhJWXI2D8g79"

# --- Timely chat test ---
print("=== Timely GPT Chat ===")
for model in ["anthropic/claude-sonnet-4.6", "anthropic/claude-haiku-4.5"]:
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with just the word: OK"}],
        "max_tokens": 10
    }).encode()
    req = urllib.request.Request(
        f"{BASE}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TIMELY_KEY}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read())
            content = result["choices"][0]["message"]["content"]
            used_model = result.get("model", "?")
            print(f"  [{model}] SUCCESS: '{content}' (actual model: {used_model})")
    except urllib.error.HTTPError as e:
        print(f"  [{model}] FAIL HTTP {e.code}: {e.read().decode()[:200]}")
    except Exception as e:
        print(f"  [{model}] ERROR: {e}")


# --- NCP auth + path test ---
print("\n=== NCP API ===")

def ncp_headers(method, path):
    ts = str(int(time.time() * 1000))
    msg = f"{method} {path}\n{ts}\n{NCP_ACCESS_KEY}"
    sig = base64.b64encode(
        hmac.new(NCP_SECRET_KEY.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "x-ncp-apigw-timestamp": ts,
        "x-ncp-iam-access-key": NCP_ACCESS_KEY,
        "x-ncp-apigw-signature-v2": sig,
        "Content-Type": "application/json",
    }

# Cloud Insight - try various paths
insight_paths = [
    "/cw_fea/real/cw/api/rule/group/list",
    "/cw_fea/real/cw/api/v1/rule/group/list",
    "/cw_fea/real/cw/api/v2/rule/group/list",
    "/cw_fea/real/cw/api/event/search",
    "/cw_fea/real/cw/api/server/top",
]
for path in insight_paths:
    try:
        req = urllib.request.Request(
            f"https://cw.apigw.ntruss.com{path}",
            headers=ncp_headers("GET", path)
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"  [Insight] {path} -> HTTP {r.status}: {r.read().decode()[:100]}")
    except urllib.error.HTTPError as e:
        print(f"  [Insight] {path} -> HTTP {e.code}: {e.read().decode()[:100]}")
    except Exception as e:
        print(f"  [Insight] {path} -> ERROR: {e}")

# CLA log count
cla_paths = [
    "/api/v1/cw/region/KR/logs/count/total",
    "/api/v2/cw/region/KR/logs/count/total",
    "/cla/v1/cw/region/KR/logs/count/total",
]
for path in cla_paths:
    try:
        req = urllib.request.Request(
            f"https://cloudloganalytics.apigw.ntruss.com{path}",
            headers=ncp_headers("GET", path)
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"  [CLA] {path} -> HTTP {r.status}: {r.read().decode()[:100]}")
    except urllib.error.HTTPError as e:
        print(f"  [CLA] {path} -> HTTP {e.code}: {e.read().decode()[:100]}")
    except Exception as e:
        print(f"  [CLA] {path} -> ERROR: {e}")
