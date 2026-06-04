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
- **폐쇄망:** relay-server가 로그까지 중계 → 모드 A로 커버.

## 보안 — 사설 subnet 필수

**VictoriaMetrics/VictoriaLogs는 기본 인증이 없다.** 절대 공인망에 노출 금지.
- [서버1]·[서버2]는 **같은 사설 subnet**. 분석서버↔VictoriaLogs 쿼리, Alertmanager→분석서버 webhook은 모두 사설 hop.
- 공인 노출은 **에이전트 ingress(monitoring_msp)** 와 **분석 대시보드** 뿐.

## 디렉토리 구성요소 (현 스캐폴드)

| 경로 | 역할 | 최종안 상태 |
|------|------|------------|
| `alloy/config.alloy` | Linux 베이스라인 (메트릭 + journald + /var/log) | 존속 (로그를 중앙 VictoriaLogs로 push) |
| `alloy/config.docker.alloy` | 컨테이너 호스트 로그 오버레이 | 존속 |
| `alloy/config.windows.alloy` | Windows 베이스라인 (메트릭 + 이벤트로그) | 존속 |
| `docker-compose.poc.yml` | VM/VictoriaLogs/vmalert/bridge | **재정렬**: VictoriaLogs만 로컬 PoC용 존속, vmalert는 monitoring_msp 것 사용, **bridge 제거** |
| `bridge/` | (구) vmalert→OBS 업로드 | **제거 예정** — 로직은 분석서버 앱(`src/`)으로 흡수(`/webhook/alert` + `victorialogs_collector`) |

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

로컬에선 사설 subnet을 **같은 docker network**로 흉내. 순서:
1. VictoriaLogs 기동 + Alloy로 로그 push 확인 (LogsQL 쿼리로 조회 검증).
2. 분석서버 앱에 `POST /webhook/alert` + `victorialogs_collector` 추가 (다음 빌드 단계).
3. monitoring_msp Alertmanager(또는 합성 webhook) → `/webhook/alert` → 5분 쿼리 → claude 분석 → 대시보드/이메일 e2e.
