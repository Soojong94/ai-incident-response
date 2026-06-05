# 설치 런북 — web(직접·게이트웨이) + was(폐쇄망)

실 테스트용 처음부터 설치 절차. 용어: **직접**(인터넷O) / **게이트웨이**(직접 중 폐쇄망 중계 겸용) / **폐쇄망**(인터넷X).
여기선 **web = 게이트웨이(자기 수집 + was 중계), was = 폐쇄망(web 경유)**.

```
[web]  Alloy(게이트웨이: 자기 메트릭+로그 + was 중계) ──인터넷──► 중앙(8428/9428)
   ▲ 사내망 :9999(메트릭) :9998(로그)
[was]  Alloy(폐쇄망, 인터넷 차단)
```

## 사전값 (미리 채워두기)

| 값 | 예시 |
|----|------|
| 중앙 IP | `101.79.23.149` |
| 대시보드 | `https://tbit-msp.kro.kr` |
| web 공인 IP | `<WEB_PUB_IP>` (중앙 ACG 허용용) |
| web 사내 IP | `<WEB_LAN_IP>` (was가 가리킴) |

---

## 0. 중앙 준비 (HTTPS 인제스트 + ACG)

에이전트 로그/메트릭은 **평문 8428/9428이 아니라 443(TLS)** 으로 받습니다 — nginx가 TLS 종단 +
basic-auth 검증 후 내부로 전달(로그가 평문으로 인터넷을 타지 않음). 중앙 서버에서:

**(1) 인제스트 자격 생성** (`/opt/ai-incident-response`):
```bash
htpasswd -bc nginx/ingest.htpasswd agent '<강한-비밀번호>'   # 없으면: apt-get install -y apache2-utils
docker compose up -d --build && docker compose restart nginx
```

**(2) ACG**: web 공인 IP로 **443만** 허용. (8428/9428은 공개 불필요 — 닫아두는 게 안전)

| 프로토콜 | 소스 | 포트 |
|---|---|---|
| TCP | `<WEB_PUB_IP>/32` | `443` (TLS 인제스트 + 대시보드) |

---

## 1. web 설치 (인터넷 O)

### 1-1. Alloy 다운로드
```bash
sudo apt-get install -y curl unzip          # rocky: sudo dnf install -y unzip
curl -sL -o /tmp/alloy.zip https://github.com/grafana/alloy/releases/latest/download/alloy-linux-amd64.zip
sudo unzip -oq /tmp/alloy.zip -d /usr/local/bin/ && sudo chmod +x /usr/local/bin/alloy-linux-amd64
```

### 1-2. config 배치 — `/etc/alloy-air/config.alloy`
```bash
sudo mkdir -p /etc/alloy-air /var/lib/alloy-air
sudo tee /etc/alloy-air/config.alloy >/dev/null <<'EOF'
// web = 자기 수집(메트릭+로그) + was 중계 겸용
prometheus.exporter.unix "self" { }
prometheus.scrape "self" {
  targets = prometheus.exporter.unix.self.targets
  forward_to = [prometheus.relabel.self.receiver]
  scrape_interval = "15s"
}
prometheus.relabel "self" {
  forward_to = [prometheus.remote_write.central.receiver]
  rule {
    target_label = "host"
    replacement  = sys.env("RESOURCE_NAME")
  }
}
local.file_match "self_logs" { path_targets = [{ "__path__" = "/var/log/**/*.log" }] }
loki.source.file "self_logs" {
  targets = local.file_match.self_logs.targets
  forward_to = [loki.process.self_logs.receiver]
}
loki.process "self_logs" {
  forward_to = [loki.write.central.receiver]
  stage.static_labels { values = { host = sys.env("RESOURCE_NAME"), job = "syslog" } }
}
// 내부 was 중계 수신
prometheus.receive_http "in" {
  http {
    listen_address = "0.0.0.0"
    listen_port    = 9999
  }
  forward_to = [prometheus.remote_write.central.receiver]
}
prometheus.remote_write "central" {
  endpoint {
    url = sys.env("CENTRAL_VM_URL")
    basic_auth {
      username = sys.env("INGEST_USER")
      password = sys.env("INGEST_PASS")
    }
  }
}
loki.source.api "in" {
  http {
    listen_address = "0.0.0.0"
    listen_port    = 9998
  }
  forward_to = [loki.write.central.receiver]
}
loki.write "central" {
  endpoint {
    url = sys.env("CENTRAL_VL_URL")
    basic_auth {
      username = sys.env("INGEST_USER")
      password = sys.env("INGEST_PASS")
    }
  }
}
EOF
```

### 1-3. 환경변수 + systemd 서비스
```bash
sudo tee /etc/default/alloy-air >/dev/null <<'EOF'
RESOURCE_NAME=web
CENTRAL_VM_URL=https://tbit-msp.kro.kr/vm/api/v1/write
CENTRAL_VL_URL=https://tbit-msp.kro.kr/vl/insert/loki/api/v1/push
INGEST_USER=agent
INGEST_PASS=<강한-비밀번호>
EOF

sudo tee /etc/systemd/system/alloy-air.service >/dev/null <<'EOF'
[Unit]
Description=Grafana Alloy (AI incident agent)
After=network-online.target
[Service]
EnvironmentFile=/etc/default/alloy-air
ExecStart=/usr/local/bin/alloy-linux-amd64 run /etc/alloy-air/config.alloy --storage.path=/var/lib/alloy-air --server.http.listen-addr=127.0.0.1:12345
Restart=always
User=root
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now alloy-air
sudo systemctl status alloy-air --no-pager   # active(running) 확인
```

### 1-4. web 사내 방화벽 — was만 9999/9998 허용
```bash
# 예) ufw 사용 시
sudo ufw allow from <WAS_LAN_IP> to any port 9999 proto tcp
sudo ufw allow from <WAS_LAN_IP> to any port 9998 proto tcp
# (firewalld: firewall-cmd --add-rich-rule='rule family=ipv4 source address=<WAS_LAN_IP> port port=9999 protocol=tcp accept' 등)
```

---

## 2. was 설치 (인터넷 X) — **web 경유로 파일 전달**

was는 인터넷이 없으니 web에서 **바이너리 + config를 복사**해 넣습니다.

### 2-1. web에서 was로 전송 (web에서 실행)
```bash
scp /usr/local/bin/alloy-linux-amd64 <was계정>@<WAS_LAN_IP>:/tmp/
```

### 2-2. was에서 설치
```bash
sudo install -m 0755 /tmp/alloy-linux-amd64 /usr/local/bin/alloy-linux-amd64
sudo mkdir -p /etc/alloy-air /var/lib/alloy-air

sudo tee /etc/alloy-air/config.alloy >/dev/null <<'EOF'
// was = 폐쇄망 → web(게이트웨이)로만 전송
prometheus.exporter.unix "n" { }
prometheus.scrape "n" {
  targets = prometheus.exporter.unix.n.targets
  forward_to = [prometheus.relabel.h.receiver]
  scrape_interval = "15s"
}
prometheus.relabel "h" {
  forward_to = [prometheus.remote_write.relay.receiver]
  rule {
    target_label = "host"
    replacement  = sys.env("RESOURCE_NAME")
  }
}
prometheus.remote_write "relay" { endpoint { url = sys.env("GATEWAY_VM_URL") } }
local.file_match "f" { path_targets = [{ "__path__" = "/var/log/**/*.log" }] }
loki.source.file "f" {
  targets = local.file_match.f.targets
  forward_to = [loki.process.h.receiver]
}
loki.process "h" {
  forward_to = [loki.write.relay.receiver]
  stage.static_labels { values = { host = sys.env("RESOURCE_NAME"), job = "syslog" } }
}
loki.write "relay" { endpoint { url = sys.env("GATEWAY_VL_URL") } }
EOF

sudo tee /etc/default/alloy-air >/dev/null <<'EOF'
RESOURCE_NAME=was
GATEWAY_VM_URL=http://<WEB_LAN_IP>:9999/api/v1/metrics/write
GATEWAY_VL_URL=http://<WEB_LAN_IP>:9998/loki/api/v1/push
EOF

# systemd 서비스는 web의 1-3과 동일 (EnvironmentFile/ExecStart 그대로)
sudo tee /etc/systemd/system/alloy-air.service >/dev/null <<'EOF'
[Unit]
Description=Grafana Alloy (AI incident agent)
After=network-online.target
[Service]
EnvironmentFile=/etc/default/alloy-air
ExecStart=/usr/local/bin/alloy-linux-amd64 run /etc/alloy-air/config.alloy --storage.path=/var/lib/alloy-air --server.http.listen-addr=127.0.0.1:12345
Restart=always
User=root
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now alloy-air
sudo systemctl status alloy-air --no-pager
```

> `<WEB_LAN_IP>`/`<WAS_LAN_IP>`는 사내망 IP. was는 web의 9999/9998로만 나가면 됩니다.

---

## 3. 도착 확인 (중앙에서, 또는 운영자가 봐줄 것)

TLS 인제스트 경로로 조회 — 두 host가 보이면 성공:
```bash
curl -s -u agent:'<비밀번호>' 'https://tbit-msp.kro.kr/vm/api/v1/query' --data-urlencode 'query=node_uname_info' | grep -o '"host":"[^"]*"' | sort -u
# "host":"web"  "host":"was"  둘 다 나오면 OK (was가 web 게이트웨이 경유로 도달)
```
로그:
```bash
curl -s -u agent:'<비밀번호>' 'https://tbit-msp.kro.kr/vl/select/logsql/query' --data-urlencode 'query=host:="was" _time:10m' | head
```

---

## 4. 대시보드 작업 ( https://tbit-msp.kro.kr )

1. **로그인** (admin 계정).
2. **사이트 관리** — 메트릭/로그가 들어오기 시작하면, 첫 장애 발생 시 `web`·`was` 사이트가 **자동 생성**됩니다.
   미리 만들고 싶으면 "+ 사이트"로 이름 `web`/`was` 생성(패턴은 비워도 됨 — 이름이 곧 host).
3. **수신자 등록** (이메일 받으려면 필수): 각 사이트 → 사이트 수정 → 수신자 추가 → 이메일 입력 + **심각도(C/H/M/L) 체크**.
4. (선택) **아키텍처** 입력 → AI 분석 정확도↑. **알림 폭주 방지**(rate-limit) 조정.
5. **장애 확인**: 임계 초과(예 CPU 80% 5분) 시 자동으로 장애 목록에 뜨고, 클릭하면 직전 5분 로그 + AI 분석.

### 트리거 테스트 (web 또는 was에서)
```bash
sudo apt-get install -y stress-ng 2>/dev/null || sudo dnf install -y stress-ng
stress-ng --cpu 0 --timeout 360s    # 6분간 CPU 부하 → HighCPUUsage 발화 → 장애 자동 등록
```
(was는 인터넷이 없어도 부하는 로컬에서 주면 됩니다 — 메트릭은 web 경유로 중앙 도달.)

---

## 체크리스트

- [ ] 중앙: `htpasswd` 생성 + ACG에 web 공인 IP로 **443**만 허용 (8428/9428 미공개)
- [ ] web: Alloy 실행(active), .env에 INGEST_USER/PASS, 사내 9999/9998 was 허용
- [ ] was: web에서 바이너리 복사, Alloy 실행(active), GATEWAY_*가 web 사내 IP
- [ ] 중앙에서 host=web, host=was 메트릭 확인 (https `/vm/` 조회)
- [ ] 대시보드: 사이트 자동생성 + 수신자 등록
- [ ] CPU 부하 → 장애 자동 등록 + 분석/이메일
