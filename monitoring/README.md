# 에이전트 기반 PoC — 관측 스택 (monitoring/)

NCP-native 수집(고객별 CF+NAT+CLA)을 폐기하고 **에이전트 기반(vendor-neutral)** 으로 전환하는 PoC.
설계·결정 배경: [`../docs/agent-based-pivot.md`](../docs/agent-based-pivot.md)

```
[서버] Alloy ──► VictoriaMetrics(메트릭) + VictoriaLogs(로그)
                         │
                vmalert(CPU>80% 5분) ──직접 webhook──► 브리지
                         └► VictoriaLogs 직전5분 쿼리 → JSONL → 중앙 OBS(tbit-air) PUT
   ───────────── 여기서부터 기존 그대로(무변경) ─────────────
   → OBS Object Created → 중앙 CF(obs-to-webhook) → /webhook/alarm → claude 분석 → 대시보드/이메일
```

## 구성요소

| 서비스 | 역할 | 포트 |
|--------|------|------|
| victoriametrics | 메트릭 저장(Alloy remote_write 수신) | 8428 |
| victorialogs | 로그 저장(Loki push 수신, LogsQL 쿼리) | 9428 |
| vmalert | `CPU>80% 5분` 룰 평가 → 브리지로 직접 통보 | 8880 |
| **bridge** | vmalert 알람 → VictoriaLogs 직전5분 → JSONL → 중앙 OBS PUT | 8080 |
| alloy | (서버측) 호스트 메트릭+로그 수집 → 중앙 push | — |

## 로컬 실행

```bash
# 레포 루트에서 (.env 의 NCP_ACCESS_KEY/NCP_SECRET_KEY = 중앙 루트 키 사용)
docker compose -f monitoring/docker-compose.poc.yml --env-file .env up -d --build
docker compose -f monitoring/docker-compose.poc.yml ps
```

> ⚠ **이미지 태그**: compose의 태그는 작성 시점 기준. `up` 실패 시 유효한 최신 태그로 조정.
> ⚠ **boto3 region**: NCP OBS는 region 없이 동작(기존 `obs_collector`와 동일). 오류 시 `region_name` 추가.

## ⚠ 실서비스 부작용 주의

브리지는 **실제 중앙 OBS(`tbit-air`)** 에 업로드한다. 그러면 **라이브 중앙 CF → /webhook/alarm
→ 실제 claude 분석 → 실제 이메일 발송**까지 자동 진행된다. 테스트 업로드도 진짜 incident가 된다.
검증 후 테스트 객체/incident 정리(`docs/agent-based-pivot.md` §9) 필요.

## 단계별 검증

### 1) 체인만 먼저 (메트릭 불필요 — Windows 로컬 권장)
브리지에 **합성 알람**을 직접 POST해서 `브리지→OBS→CF→분석` 전 구간을 검증:

```bash
curl -X POST http://localhost:8080/api/v2/alerts \
  -H "Content-Type: application/json" \
  -d '[{"status":"firing","labels":{"alertname":"HighCPU","host":"tbit-air-mon-01"},"startsAt":"2026-06-02T09:00:00Z"}]'
```
→ 브리지 로그에 `업로드 완료 host=... key=...` 확인 → 대시보드에 incident 생성 확인.
(VictoriaLogs에 해당 host 로그가 없으면 0줄로 업로드됨 — graceful degradation)

### 2) 로그 파이프라인 (Alloy)
`alloy/config.alloy`로 Alloy 실행(로그 tail → VictoriaLogs). 환경변수:
`RESOURCE_NAME`, `VL_LOKI_PUSH_URL=http://localhost:9428/insert/loki/api/v1/push`, `LOG_PATH`.
이후 1)의 합성 알람을 다시 쏘면 실제 로그 5분치가 JSONL로 담긴다.

### 3) 메트릭 기반 자동 알람 (Linux 테스트 서버)
`prometheus.exporter.unix`는 **Linux 전용**. 진짜 `CPU>80% 5분` 자동 발화는 Linux 서버에
Alloy 설치 후 부하를 유발해 검증한다. (Windows 로컬은 1)·2)로 체인·로그까지만 검증)

## 서버(tbit-air-pub) 배포 시 (PoC 통과 후)

- 이 4개 서비스를 메인 `../docker-compose.yml`로 병합(결정 #1).
- Alloy → 중앙 수집 인입을 nginx 리버스프록시(`/vm/...`, `/vl/...`) + **basic-auth**(기존 LE TLS 재사용)로 노출.
  서버 아웃바운드만으로 도달 → 고객측 NAT/CF 불필요.
- 고객 온보딩 = 서버에 **Alloy 1개 설치 + RESOURCE_NAME 지정**.
