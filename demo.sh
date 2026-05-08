#!/bin/bash
# 장애 시뮬레이션 시작/종료
# 사용법: ./demo.sh start | stop

ACTION=${1:-start}

if [ "$ACTION" = "start" ]; then
    cat > /etc/nginx/sites-enabled/default << 'EOF'
server {
    listen 80 default_server;
    server_name _;
    error_log /var/log/nginx/error.log;
    access_log /var/log/nginx/access.log;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_connect_timeout 2s;
        proxy_read_timeout 2s;
    }
}
EOF
    nginx -s reload
    echo "장애 시작 — 약 2~3분 후 https://tbit-msp.kro.kr 에서 분석 결과 확인"

elif [ "$ACTION" = "stop" ]; then
    cat > /etc/nginx/sites-enabled/default << 'EOF'
server {
    listen 80 default_server;
    server_name _;
    error_log /var/log/nginx/error.log;
    access_log /var/log/nginx/access.log;
    location / {
        return 200 "OK";
    }
}
EOF
    nginx -s reload
    echo "복구 완료"
fi
