"""Quick API connectivity test - Timely GPT + NCP Cloud Insight/CLA."""

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request

TIMELY_KEY = "tgpt_sk_18f4cd3c098e58212bcc44f937999ed6d1486c5c4822a667f669525a05ea0ab8"
TIMELY_BASE = "https://hello.timelygpt.co.kr/api/v2/chat/bridge/openai"

NCP_ACCESS_KEY = "ncp_iam_BPAMKR5Yp8saADhOAwrQ"
NCP_SECRET_KEY = "ncp_iam_BPKMKRKI5mMOW7glW8OfVYbhJWXI2D8g79"
NCP_INSIGHT_BASE = "https://cw.apigw.ntruss.com"
NCP_CLA_BASE = "https://cloudloganalytics.apigw.ntruss.com"


# ──────────────────────────────────────────────
# Timely GPT
# ──────────────────────────────────────────────

def test_timely_models():
    url = f"{TIMELY_BASE}/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TIMELY_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            models = [m["id"] for m in data.get("data", [])]
            claude = [m for m in models if "claude" in m.lower() or "anthropic" in m.lower()]
            print(f"✓ Timely 모델 조회 성공 - 전체 {len(models)}개")
            print(f"  └ Claude 계열: {claude[:5]}")
    except urllib.error.HTTPError as e:
        print(f"✗ Timely 모델 조회 실패 HTTP {e.code}: {e.read().decode()[:300]}")
    except Exception as e:
        print(f"✗ Timely 연결 실패: {e}")


def test_timely_chat():
    url = f"{TIMELY_BASE}/chat/completions"
    payload = json.dumps({
        "model": "anthropic/claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "두 단어 이내로만 대답해: 테스트 성공?"}],
        "max_tokens": 20,
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TIMELY_KEY}",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            content = result["choices"][0]["message"]["content"]
            model_used = result.get("model", "?")
            print(f"✓ Timely 채팅 성공 (모델: {model_used})")
            print(f"  └ 응답: '{content}'")
    except urllib.error.HTTPError as e:
        print(f"✗ Timely 채팅 실패 HTTP {e.code}: {e.read().decode()[:300]}")
    except Exception as e:
        print(f"✗ Timely 채팅 실패: {e}")


# ──────────────────────────────────────────────
# NCP 공통 인증
# ──────────────────────────────────────────────

def _ncp_headers(method: str, path: str) -> dict:
    timestamp = str(int(time.time() * 1000))
    message = f"{method} {path}\n{timestamp}\n{NCP_ACCESS_KEY}"
    sig = base64.b64encode(
        hmac.new(NCP_SECRET_KEY.encode(), message.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "x-ncp-apigw-timestamp": timestamp,
        "x-ncp-iam-access-key": NCP_ACCESS_KEY,
        "x-ncp-apigw-signature-v2": sig,
        "Content-Type": "application/json",
    }


def _get(base_url: str, path: str) -> tuple[int, str]:
    req = urllib.request.Request(f"{base_url}{path}", headers=_ncp_headers("GET", path))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode()[:400]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]
    except Exception as e:
        return 0, str(e)


# ──────────────────────────────────────────────
# NCP Cloud Insight
# ──────────────────────────────────────────────

def test_ncp_insight():
    # Event Rule 목록 조회 (GetRuleGroupList)
    paths_to_try = [
        "/cw_fea/real/cw/api/rule/group/list",
        "/cw_fea/real/cw/api/v1/rule/group/list",
    ]
    for path in paths_to_try:
        code, body = _get(NCP_INSIGHT_BASE, path)
        if code == 200:
            print(f"✓ NCP Cloud Insight 연결 성공 ({path})")
            print(f"  └ {body[:200]}")
            return
        elif code in (401, 403):
            print(f"✗ NCP Cloud Insight 인증 실패 HTTP {code} ({path})")
            print(f"  └ {body[:200]}")
            print("  └ Secret Key를 확인하세요 (Access Key와 동일한 값으로 설정됨)")
            return
        else:
            print(f"  ? Cloud Insight {path} → HTTP {code}: {body[:100]}")


# ──────────────────────────────────────────────
# NCP Cloud Log Analytics
# ──────────────────────────────────────────────

def test_ncp_cla():
    paths_to_try = [
        "/api/v1/cw/region/KR/logs/count/total",
        "/api/v2/cw/region/KR/logs/count/total",
        "/api/v1/logs/count/total",
    ]
    for path in paths_to_try:
        code, body = _get(NCP_CLA_BASE, path)
        if code == 200:
            print(f"✓ NCP Cloud Log Analytics 연결 성공 ({path})")
            print(f"  └ {body[:200]}")
            return
        elif code in (401, 403):
            print(f"✗ NCP CLA 인증 실패 HTTP {code} ({path})")
            print(f"  └ {body[:200]}")
            return
        else:
            print(f"  ? CLA {path} → HTTP {code}: {body[:100]}")


# ──────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("API 연결 테스트")
    print("=" * 50)

    print("\n[1] Timely GPT")
    test_timely_models()
    test_timely_chat()

    print("\n[2] NCP Cloud Insight")
    test_ncp_insight()

    print("\n[3] NCP Cloud Log Analytics")
    test_ncp_cla()

    print("\n완료")
