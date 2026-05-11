#!/bin/bash
# team1-test-server (Ubuntu) 에서 실행 — nginx를 500 에러 응답 모드로 전환.
# WMS 시나리오의 errorCount를 증가시켜 데모 흐름을 트리거하기 위함.
# 사용법: sudo ./start_error.sh
set -euo pipefail

cat > /etc/nginx/sites-enabled/default << 'EOF'
server {
    listen 80 default_server;
    server_name _;
    error_log /var/log/nginx/error.log;
    access_log /var/log/nginx/access.log;
    location / {
        add_header Content-Type text/plain;
        return 500 "Internal Server Error";
    }
}
EOF

nginx -t
nginx -s reload
echo "장애 시작 — curl -i http://localhost/ 로 500 응답 확인"
