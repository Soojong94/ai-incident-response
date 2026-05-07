"""
NCP Cloud Insight 인증 + SearchEvent 실제 테스트.
목적: HMAC 서명이 실제로 통과하는지 확인 (CLA 없어도 Cloud Insight는 응답해야 함).
"""
import base64
import hashlib
import hmac
import json
import time
import urllib.request
import urllib.error

NCP_ACCESS_KEY = "ncp_iam_BPAMKR5Yp8saADhOAwrQ"
NCP_SECRET_KEY = "ncp_iam_BPKMKRKI5mMOW7glW8OfVYbhJWXI2D8g79"
INSIGHT_BASE   = "https://cw.apigw.ntruss.com"


def ncp_headers(method: str, path: str) -> dict:
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


def post(path: str, body: dict):
    url = f"{INSIGHT_BASE}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=ncp_headers("POST", path))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]
    except Exception as e:
        return 0, str(e)


now = int(time.time())
one_hour_ago = now - 3600

# 1. SearchEvent (POST) — Cloud Insight 이벤트 조회
print("[1] Cloud Insight SearchEvent (POST)")
print(f"    startTime={one_hour_ago}, endTime={now}")
code, body = post("/cw_fea/real/cw/api/event/search", {
    "startTime": str(one_hour_ago),
    "endTime": str(now),
})
print(f"    HTTP {code}")
if isinstance(body, dict):
    print(f"    {json.dumps(body, ensure_ascii=False)[:300]}")
else:
    print(f"    {body[:300]}")

# 2. GetServersTop (GET) — Cloud Insight 서버 상위 조회
print("\n[2] Cloud Insight GetServersTop (GET)")
path = "/cw_fea/real/cw/api/server/top"
req = urllib.request.Request(
    f"{INSIGHT_BASE}{path}",
    headers=ncp_headers("GET", path)
)
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        print(f"    HTTP {r.status}: {r.read().decode()[:200]}")
except urllib.error.HTTPError as e:
    print(f"    HTTP {e.code}: {e.read().decode()[:300]}")
except Exception as e:
    print(f"    ERROR: {e}")

# 3. GetRuleGroupList — 올바른 경로 찾기 시도
print("\n[3] GetRuleGroupList 경로 탐색")
for path in [
    "/cw_fea/real/cw/api/rule/group/list",
    "/cw_fea/real/cw/api/v1/rule/group/list",
    "/cw_fea/real/cw/api/rule/group",
]:
    req = urllib.request.Request(
        f"{INSIGHT_BASE}{path}",
        headers=ncp_headers("GET", path)
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            print(f"    {path} -> HTTP {r.status} OK")
    except urllib.error.HTTPError as e:
        print(f"    {path} -> HTTP {e.code}: {e.read().decode()[:100]}")
    except Exception as e:
        print(f"    {path} -> ERROR: {e}")
