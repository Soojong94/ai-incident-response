# 시스템 아키텍처

## 전체 흐름

```
┌──────────────────────────────────────────────────────┐
│                   NCP 클라우드 인프라                  │
│  [고객사 서버]  →  [Cloud Insight]  →  Alarm 발생     │
└─────────────────────────┬────────────────────────────┘
                          │ HTTP POST (Webhook)
                          ↓
┌──────────────────────────────────────────────────────┐
│                    중앙 서버 (FastAPI)                  │
│                                                       │
│  ┌─────────────┐   ┌──────────────┐   ┌───────────┐  │
│  │  Webhook    │→  │  Collector   │→  │ Analyzer  │  │
│  │  Receiver   │   │  (로그수집)   │   │ (Claude)  │  │
│  └─────────────┘   └──────────────┘   └─────┬─────┘  │
│         │                 │                  │        │
│         ↓                 ↓                  ↓        │
│  ┌──────────────────────────────────────────────────┐ │
│  │                    DB (SQLite)                    │ │
│  │  incidents / logs / analysis_results              │ │
│  └──────────────────────────────────────────────────┘ │
│                                                       │
│  ┌─────────────┐                   ┌───────────────┐  │
│  │  Responder  │                   │    Web UI     │  │
│  │  (Slack)    │                   │  (대시보드)    │  │
│  └─────────────┘                   └───────────────┘  │
└──────────────────────────────────────────────────────┘
          │
          ↓
   [Slack Channel]   [담당자 확인]
```

---

## 컴포넌트 상세

### 1. Webhook Receiver (`src/webhook/`)

- 엔드포인트: `POST /webhook/alarm`
- HMAC-SHA256 서명 검증
- 알람 페이로드 파싱 및 정규화
- `Incident` 레코드 DB 생성
- 비동기로 Collector 파이프라인 시작

**Cloud Insight Alarm 페이로드 예시**
```json
{
  "alarmName": "CPU-High-Alert",
  "serverName": "web-01",
  "serverIp": "192.168.1.10",
  "metric": "cpu_used_rto",
  "threshold": 85,
  "currentValue": 92.4,
  "timestamp": "2026-05-07T14:32:00+09:00",
  "severity": "CRITICAL"
}
```

---

### 2. Collector (`src/collector/`)

#### 2-1. NCP Log Analytics API
```
GET /api/v1/logs
  ?startTime={alarm_time - 15min}
  &endTime={alarm_time + 15min}
  &serverIp={target_ip}
```

#### 2-2. SSH 로그 Pull (fallback)
```python
# paramiko 사용
# /var/log/messages, /var/log/syslog
# 앱 로그 경로는 고객사 프로파일에서 조회
```

#### 2-3. 메트릭 수집
- Cloud Insight Metric API로 알람 전후 30분 메트릭 시계열 수집
- CPU, Memory, Disk I/O, Network 4개 항목

---

### 3. Analyzer (`src/analyzer/`)

#### Claude API 호출 흐름
```
1. 수집된 데이터 컨텍스트 패키징
2. system prompt (SRE 역할) + user prompt (알람+로그+메트릭)
3. Claude API 호출 (tool use로 구조화 응답 강제)
4. 결과 파싱 및 DB 저장
```

#### 토큰 관리
- 로그 최대 8,000 토큰으로 잘라내기 (긴 로그는 에러 라인 우선 추출)
- 메트릭은 5분 평균으로 집계 후 전달
- 전체 입력 토큰 목표: 10,000 이하

---

### 4. Responder (`src/responder/`)

#### Slack 메시지 구조
```
🚨 [CRITICAL] CPU 과부하 - web-01

📊 알람 정보
• 서버: web-01 (192.168.1.10)
• 항목: CPU 사용률 92.4% (임계값: 85%)
• 발생: 2026-05-07 14:32

🤖 AI 분석 결과
• 원인: 애플리케이션 오류 - 특정 프로세스 CPU 독점
• 영향: 웹 서버 응답 지연 예상

⚡ 즉시 조치
1. top/htop으로 CPU 점유 프로세스 확인
2. 해당 프로세스 재시작 또는 종료
3. 애플리케이션 로그 무한루프 패턴 확인

🔗 상세 분석: https://central-server/incidents/42
```

---

### 5. DB 스키마 (`src/db/`)

```sql
-- 장애 이력
CREATE TABLE incidents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alarm_name TEXT,
  server_name TEXT,
  server_ip TEXT,
  metric TEXT,
  threshold REAL,
  current_value REAL,
  severity TEXT,
  occurred_at DATETIME,
  status TEXT DEFAULT 'open',  -- open / analyzing / resolved
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 수집된 로그
CREATE TABLE incident_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id INTEGER REFERENCES incidents(id),
  log_source TEXT,  -- ncp_api / ssh
  log_content TEXT,
  collected_at DATETIME
);

-- AI 분석 결과
CREATE TABLE analysis_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id INTEGER REFERENCES incidents(id),
  cause_category TEXT,
  cause_detail TEXT,
  severity TEXT,
  impact_scope TEXT,
  immediate_actions TEXT,  -- JSON array
  prevention TEXT,
  raw_response TEXT,
  analyzed_at DATETIME
);
```

---

## 배포 구성

```
중앙 서버 (NCP Server 또는 로컬)
  └── uvicorn main:app --host 0.0.0.0 --port 8000
        ├── /webhook/alarm  ← Cloud Insight가 호출
        ├── /incidents      ← 웹 UI
        └── /static         ← CSS/JS
```

외부에서 Webhook을 받으려면 공인 IP 또는 NCP Load Balancer 필요.
개발/데모 환경에서는 ngrok으로 로컬 터널링 가능.
