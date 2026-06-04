#!/usr/bin/env bash
# Rocky/Ubuntu 컨테이너에서 Alloy(리눅스 바이너리)를 설치·실행해 중앙으로 메트릭+로그 전송.
# 사용: agent.sh <HOST_LABEL>   (CPU 부하/crash 로그는 docker exec로 별도 트리거)
set -e
HOST="${1:?HOST label required}"
CENTRAL="101.79.23.149"

echo "[agent] $HOST — deps 설치"
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y -qq curl unzip procps >/dev/null
elif command -v dnf >/dev/null 2>&1; then
  # rocky9는 curl-minimal이 이미 curl 제공 → curl 재설치 시 충돌. unzip/procps-ng만.
  dnf install -y -q unzip procps-ng >/dev/null
fi

echo "[agent] $HOST — Alloy 바이너리 다운로드"
curl -sL -o /tmp/alloy.zip https://github.com/grafana/alloy/releases/latest/download/alloy-linux-amd64.zip
unzip -oq /tmp/alloy.zip -d /usr/local/bin/
chmod +x /usr/local/bin/alloy-linux-amd64
mkdir -p /var/log /var/lib/alloy
echo "$(date -u +%FT%TZ) INFO [app] $HOST agent started" > /var/log/app.log

cat > /etc/config.alloy <<EOF
prometheus.exporter.unix "n" { }

prometheus.scrape "n" {
  targets         = prometheus.exporter.unix.n.targets
  forward_to      = [prometheus.relabel.h.receiver]
  scrape_interval = "15s"
}

prometheus.relabel "h" {
  forward_to = [prometheus.remote_write.c.receiver]
  rule {
    target_label = "host"
    replacement  = "$HOST"
  }
}

prometheus.remote_write "c" {
  endpoint { url = "http://$CENTRAL:8428/api/v1/write" }
}

local.file_match "f" {
  path_targets = [{ "__path__" = "/var/log/app.log" }]
}

loki.source.file "f" {
  targets    = local.file_match.f.targets
  forward_to = [loki.process.h.receiver]
}

loki.process "h" {
  forward_to = [loki.write.c.receiver]
  stage.static_labels {
    values = { host = "$HOST", job = "applog" }
  }
}

loki.write "c" {
  endpoint { url = "http://$CENTRAL:9428/insert/loki/api/v1/push" }
}
EOF

echo "[agent] $HOST — Alloy 실행"
exec /usr/local/bin/alloy-linux-amd64 run /etc/config.alloy --storage.path /var/lib/alloy --server.http.listen-addr=0.0.0.0:12345
