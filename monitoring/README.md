# 에이전트 기반 관측 (monitoring/)

NCP-native 수집(Cloud Insight + CLA + OBS + Cloud Function)을 폐기하고 **에이전트 기반(vendor-neutral)** 으로
전환한 구성. **정본 설계: [`../docs/agent-based-pivot.md`](../docs/agent-based-pivot.md) §12 · 다이어그램
[`../docs/architecture.drawio`](../docs/architecture.drawio).**

> ⚠ 이 디렉토리의 초기 스캐폴드(OBS 업로드 브리지 + VictoriaLogs 쿼리 + vmalert)는 **재정렬 중**입니다.
> 최종안에서 **OBS·CF·별도 브리지는 제거**, 메트릭/알람/에이전트는 별도 시스템 `monitoring_msp` 재사용,
> 이 레포는 **"Alertmanager 알람 → VictoriaLogs 직전 5분 쿼리 → AI 분석"** 만 담당합니다.

## 최종 아키텍처

```
[고객 호스트] Alloy ──상시 push(메트릭+로그)──► 공인 Ingress(monitoring_msp, relay로 폐쇄망도)
   ┌──────────────── 사설 subnet (VPC) ────────────────┐
   │ [서버1] monitoring_msp 중앙                         │
   │   VictoriaMetrics · VictoriaLogs(7일 롤링) · vmalert · Alertmanager · Grafana │
   │   vmalert 발화 → Alertmanager ──① webhook(사설)──► [서버2]                     │
   │ [서버2] ai-incident-response 분석서버                                          │
   │   POST /webhook/alert → ② VictoriaLogs 직전5분 쿼리(pull,사설) → ③ claude 분석 │
   │   → incident DB → 웹 대시보드 + 이메일                                          │
   └────────────────────────────────────────────────────┘
```

- **모드 A (기본):** Alloy가 로그를 중앙 VictoriaLogs에 상시 전송 → 알람 시 분석서버가 직전 5분을 pull. 호스트 추가 코드 0.
- **모드 B (데이터 거주성 제약 고객):** 로그 중앙 미전송 → 호스트 폴러가 `journalctl 5분`을 분석서버로 inline POST.
- **폐쇄망:** 게이트웨이(gateway.alloy)가 메트릭+로그를 중계 → 폐쇄망 서버(closed.alloy)도 커버.

## 보안 — 사설 subnet 필수

**VictoriaMetrics/VictoriaLogs는 기본 인증이 없다.** 절대 공인망에 노출 금지.
- [서버1]·[서버2]는 **같은 사설 subnet**. 분석서버↔VictoriaLogs 쿼리, Alertmanager→분석서버 webhook은 모두 사설 hop.
- 공인 노출은 **에이전트 ingress(monitoring_msp)** 와 **분석 대시보드** 뿐.

## 디렉토리 구성요소 (현 스캐폴드)

| 경로 | 역할 | 상태 |
|------|------|------|
| `alloy/config.alloy` | Linux 베이스라인 (메트릭 + journald + /var/log) | 존속 (로그를 중앙 VictoriaLogs로 push) |
| `alloy/config.docker.alloy` | 컨테이너 호스트 로그 오버레이 | 존속 |
| `alloy/config.windows.alloy` | Windows 베이스라인 (메트릭 + 이벤트로그) | 존속 |
| `docker-compose.poc.yml` | **로컬 VictoriaLogs만** (모드 A) | 정리 완료 — VM·vmalert·Alertmanager는 monitoring_msp 담당 |
| `vmalert/alerts.yml` | `CPU>80% 5분` 룰 | **참조용** — monitoring_msp의 vmalert에 등록할 룰 정의 |
| ~~`bridge/`~~ | (구) vmalert→OBS 업로드 | **제거됨** — 로직은 분석서버 앱(`src/`)으로 흡수: [`/webhook/alert`](../src/main.py) + [`victorialogs_collector`](../src/collector/victorialogs_collector.py) |

## 로그 수집 전략 — 앱 경로를 받지 않는다

고객마다 앱 로그 디렉토리를 받는 건 비현실적 → **중앙 싱크 + 표준 위치만** 잡는다.
Alloy는 `/etc/alloy/*.alloy`를 합쳐 로드 → 베이스 + 호스트별 오버레이 드롭인.

| 파일 | 대상 | 수집 |
|------|------|------|
| `config.alloy` | 모든 Linux | unix 메트릭 + journald(systemd 전체) + `/var/log/**/*.log` |
| `config.docker.alloy` | 컨테이너 호스트 | 모든 컨테이너 stdout |
| `config.windows.alloy` | Windows | windows 메트릭 + 이벤트로그 |

- 비표준 경로 앱만 예외적 opt-in. 평소 온보딩은 무설정.
- 일관성 키: **server_name = 대시보드 사이트 ID** (Alloy 라벨 = 알람 라벨 = 조회 키).

## PoC (모드 A, 로컬)

앱 구현 완료: `POST /webhook/alert` + `victorialogs_collector`. 환경변수 `VICTORIALOGS_URL`로 조회 대상 지정.

```bash
# 1) 로컬 VictoriaLogs 기동
docker compose -f monitoring/docker-compose.poc.yml up -d

# 2) (선택) Alloy로 로그 push — alloy/config.alloy, VL_LOKI_PUSH_URL=http://localhost:9428/insert/loki/api/v1/push
#    또는 직접 주입:
curl -s -X POST 'http://localhost:9428/insert/jsonline?_stream_fields=host' \
  -H 'Content-Type: application/stream+json' \
  --data-binary '{"_time":"2026-06-04T09:00:00Z","host":"demo-01","_msg":"nginx 500 burst","job":"syslog"}'

# 3) 합성 Alertmanager 알람 → 분석서버 (VICTORIALOGS_URL=http://localhost:9428 로 앱 기동한 상태)
curl -s -X POST http://localhost:8000/webhook/alert -H 'Content-Type: application/json' -d '{
  "version":"4","status":"firing",
  "alerts":[{"status":"firing","labels":{"alertname":"HighCPU","server_name":"demo-01","severity":"critical"},
             "annotations":{"value":"93"},"startsAt":"2026-06-04T09:00:05Z"}]
}'
```
→ 대시보드에 incident 생성 → host `demo-01` 직전 5분 로그가 분석에 포함 → claude 분석/이메일.
운영에선 monitoring_msp Alertmanager의 webhook receiver가 `/webhook/alert`를 사설로 호출.
