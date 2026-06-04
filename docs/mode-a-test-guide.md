# 모드 A 실증 가이드 — 실제 로그 수집 → 분석서버 도달

목표: **테스트 서버의 진짜 로그가 Alloy로 중앙에 쌓이고, 알람 트리거 시 분석서버가 직전 5분을
쿼리해 AI 분석까지** 가는 것을 실제로 확인한다. (라이브 분석서버는 안 건드림 — 별도 박스에서 신규 코드로)

## 토폴로지 (권장: 2대)

```
[A] 테스트 서버 (모니터링 대상)        [B] 중앙 박스 (수집 + 분석, 같은 subnet)
    Grafana Alloy ──로그 push(9428)──►  VictoriaLogs ◄─쿼리─ 분석앱(/webhook/alert, :8000)
                                                              └ AI 분석 → 대시보드/이메일
```
- ②VictoriaLogs + ③분석앱을 **[B] 한 박스**에 둔다. [A]는 Alloy만.
- 포트: [A]→[B] **9428**(Alloy 로그 push), 운영자→[B] **8000**(대시보드/알람). 9428은 사설/내부에서만 열 것(무인증).

---

## [B] 중앙 박스 — VictoriaLogs + 분석앱

### 1) VictoriaLogs 기동
```bash
# 레포 클론 후
docker compose -f monitoring/docker-compose.poc.yml up -d        # victorialogs :9428
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:9428/select/logsql/query -d 'query=*'  # 200이면 OK
```

### 2) 분석앱 기동 (신규 코드)
`.env` 준비(`AI_API_KEY` 필수, SMTP는 메일 보려면). VictoriaLogs를 가리키게 `VICTORIALOGS_URL` 지정:
```bash
pip install -r requirements.txt
VICTORIALOGS_URL=http://localhost:9428 LOG_WINDOW_SECONDS=300 \
  uvicorn src.main:app --host 0.0.0.0 --port 8000
# 확인: http://<B-IP>:8000/  (로그인 페이지로 303)
#       curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/webhook/alert  → 405(라우트 존재)
```
> VictoriaLogs가 다른 박스면 `VICTORIALOGS_URL=http://<VL-사설IP>:9428`.

---

## [A] 테스트 서버 — Grafana Alloy 설치 + 로그 전송

### 1) Alloy 설치 (Ubuntu 예시)
```bash
# Grafana 저장소 추가 후
sudo apt-get install -y alloy        # 또는 바이너리 다운로드
```

### 2) config 배치 + 환경변수
[`../monitoring/alloy/config.alloy`](../monitoring/alloy/config.alloy)를 `/etc/alloy/config.alloy`로 복사.
환경변수(systemd drop-in 또는 `/etc/default/alloy`):
```
RESOURCE_NAME=test-web-01                     # ★ = 알람 server_name = 대시보드 사이트 ID (끝까지 동일해야 함)
VM_REMOTE_WRITE_URL=http://<B-IP>:8428/api/v1/write   # 메트릭(선택 — 실제 vmalert 트리거 쓸 때만)
VL_LOKI_PUSH_URL=http://<B-IP>:9428/insert/loki/api/v1/push
LOG_PATH=/var/log/syslog                       # config 기본은 journald + /var/log glob
```
```bash
sudo systemctl restart alloy && journalctl -u alloy -f   # 에러 없는지
```

### 3) 로그가 중앙에 쌓이는지 확인 ([B]에서)
```bash
curl -s 'http://localhost:9428/select/logsql/query' --data-urlencode 'query={host="test-web-01"}' | head
# test-web-01 의 최근 로그가 보이면 수집 성공
```

---

## 트리거 → 분석 도달

### 옵션 1 — 합성 알람 (로그 수집 검증에 집중, 권장)
실제 로그는 [A]→VictoriaLogs로 진짜 수집되고, **트리거만** 합성으로 쏜다(분석앱 [B]에서):
```bash
curl -s -X POST http://localhost:8000/webhook/alert -H 'Content-Type: application/json' -d '{
  "version":"4","status":"firing",
  "alerts":[{"status":"firing",
    "labels":{"alertname":"HighCPU","server_name":"test-web-01","severity":"critical"},
    "annotations":{"value":"95"},
    "startsAt":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"}]
}'
```
→ `http://<B-IP>:8000/` 대시보드에 incident 생성 + **test-web-01 직전 5분 실제 로그**가 분석에 포함.

### 옵션 2 — 진짜 vmalert 트리거 (운영 동등)
[B]에 VictoriaMetrics+vmalert+Alertmanager(= monitoring_msp 중앙)도 띄우고, Alertmanager에
`/webhook/alert` 웹훅 리시버를 추가한 뒤, [A]에서 CPU 부하를 유발해 `HighCPUUsage` 발화시킨다.
(메트릭 stack이 필요 → monitoring_msp 연동 단계에서 진행)

---

## 검증 포인트

- [B] VictoriaLogs에 `{host="test-web-01"}` 로그 적재 ✅
- 합성/실제 알람 → 분석앱 incident 생성, 상태 `analyzed` ✅
- incident 상세의 로그가 **[A]의 진짜 5분치** ✅ (source=victorialogs)
- AI 분석/이메일 정상 ✅

## 주의

- **server_name 일관성**: Alloy `RESOURCE_NAME` = 알람 `server_name` = 대시보드 사이트 = OBS 키(레거시) 첫 세그먼트. 다르면 매칭 실패.
- **9428 노출 금지**: VictoriaLogs는 무인증 → [A]↔[B] 사설 경로로만. 공인에 열지 말 것.
- 실제 AI 호출·이메일이 발생하니 테스트 후 incident 정리.
