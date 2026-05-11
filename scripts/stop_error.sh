#!/bin/bash
# team1-test-server (Ubuntu) 에서 실행 — nginx를 정상(200 OK) 응답 모드로 복구.
# 사용법: sudo ./stop_error.sh
set -euo pipefail

cat > /etc/nginx/sites-enabled/default << 'EOF'
server {
    listen 80 default_server;
    server_name _;
    error_log /var/log/nginx/error.log;
    access_log /var/log/nginx/access.log;
    location / {
        add_header Content-Type text/plain;
        return 200 "OK";
    }
}
EOF

nginx -t
nginx -s reload
echo "복구 완료 — curl -i http://localhost/ 로 200 응답 확인"
