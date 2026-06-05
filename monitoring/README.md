# 에이전트 기반 관측 (monitoring/)

NCP-native 수집(Cloud Insight + CLA + OBS + Cloud Function)을 폐기하고 **에이전트 기반(vendor-neutral)** 으로
전환한 구성. **정본 설계: [`../docs/agent-based-pivot.md`](../docs/agent-based-pivot.md) §12 · 다이어그램
[`../docs/architecture.drawio`](../docs/architecture.drawio).**

> **현행:** 중앙 스택(app·nginx·VictoriaMetrics·VictoriaLogs·vmalert·Alertmanager)을 이 레포의
> [`../docker-compose.yml`](../docker-compose.yml) 하나로 **all-in-one** 구동합니다. 에이전트는 **HTTPS 443**
> (`/vm/`,`/vl/` + basic-auth)로 전송하고, OBS·CF·별도 브리지는 제거됐습니다.

## 아키텍처 (all-in-one)

```
[대상 서버] Alloy ──HTTPS 443(/vm/ /vl/, basic-auth)──► [중앙 서버 (docker-compose)]
                                                          nginx → VictoriaMetrics / VictoriaLogs(7일)
                                                          vmalert 발화 → Alertmanager
                                                          Alertmanager ──webhook(내부)──► app /webhook/alert
                                                          app → VictoriaLogs 직전5분 pull → AI 분석 → DB/이메일
```

- **수집(기본):** Alloy가 메트릭·로그를 중앙에 상시 전송 → 알람 시 분석서버가 직전 5분을 pull. 호스트 추가 코드 0.
- **폐쇄망:** 게이트웨이(`gateway.alloy`)가 자기+폐쇄망 중계 트래픽에 `group` 라벨을 찍어 중앙으로 전달 → 폐쇄망 서버(`closed.alloy`)는 게이트웨이 사내 IP(9999/9998)로만 전송.
- **그룹:** `GROUP_NAME` 라벨 = 상위 그룹(공인IP/게이트웨이 단위) → 대시보드에서 그룹으로 묶여 표시.

## 보안 — 사설 subnet 필수

**VictoriaMetrics/VictoriaLogs는 기본 인증이 없다.** 8428/9428은 컨테이너 내부에서만 접근, 절대 공개 금지.
- 외부 인제스트는 **nginx 443**(`/vm/`,`/vl/`)으로만 — TLS 종단 + **basic-auth**([`../nginx/ingest.htpasswd`](../nginx)).
- `/webhook/alert`는 nginx에서 외부 차단(Alertmanager는 내부에서 호출). 공개 노출은 **443**(대시보드 + 인제스트)뿐.

## 디렉토리 구성요소 (현 스캐폴드)

| 경로 | 역할 | 상태 |
|------|------|------|
| `alloy/config.alloy` | Linux 베이스라인 (메트릭 + journald + /var/log) | 존속 (로그를 중앙 VictoriaLogs로 push) |
| `alloy/config.docker.alloy` | 컨테이너 호스트 로그 오버레이 | 존속 |
| `alloy/config.windows.alloy` | Windows 베이스라인 (메트릭 + 이벤트로그) | 존속 |
| `alloy/gateway.alloy` | 게이트웨이 — 자기수집 + 폐쇄망 중계 + `group` 라벨 | 존속 |
| `alloy/closed.alloy` | 폐쇄망 서버 — 게이트웨이 사내 IP로만 전송 | 존속 |
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
