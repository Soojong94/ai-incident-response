# 핸드오프 — 인프라 재구축 완료 + 에이전트 기반 전환 (2026-06-02)

> 이 문서는 2026-06-02 세션의 결과물이자 다음 세션 시작점이다.
> 맨땅(서버·VPC·CF 전부 삭제)에서 전체를 재구축해 **NCP-native 흐름을 end-to-end 실증**했고,
> 그 과정에서 드러난 **비용·NCP 종속 문제** 때문에 **에이전트 기반(vendor-neutral)** 으로 방향을 전환하기로 했다.

---

## 1. 계정 토폴로지 (혼동 주의 — 실측 확정)

| 계정 | NCP ID | 키 | 소유 버킷 | 역할 |
|------|--------|-----|----------|------|
| 중앙(관리/분석) | `ncp-2617879-0` | `8p6Q7DFa…` (root, `.env`) | `tbit-air`, contract-management-bucket | 분석 서버 + 중앙 OBS/CF |
| 개인(고객 샘플) | `ncp-3277912-0` | `ncp_iam_BPAMKR…` (IAM) | ksj-test123 | 모니터링 대상 서버 + 고객 CF |

- `list_buckets`의 owner ID로 계정 식별. 루트 키가 버킷 GET에서 AccessDenied면 **그 버킷은 다른 계정 소유**다.

## 2. 현재 배포 상태 (검증 완료)

**중앙 서버** — `tbit-air-pub` (공인 IP `101.79.23.149`, Ubuntu 24.04, 중앙 계정)
- VPC `tbit-air-vpc` `172.31.0.0/16` / subnet `tbit-air-pub-sn` `172.31.1.0/24` (KR-1) / ACG 인바운드 22·80·443 + **NAT 티어 추가됨**
- Docker compose: `app`(FastAPI :8000) + `nginx`(:80/:443, Let's Encrypt **webroot 자동갱신** 구성 완료)
- 경로 `/opt/ai-incident-response`, 데이터 `/opt/ai-incident-response/data`
- AI: `claude-sonnet-4-6` (Timely 경유), 호출 실패 1회 재시도, Gmail SMTP 이메일 정상
- `https://tbit-msp.kro.kr` 라이브 (DNS → 101.79.23.149)

**중앙 CF** — `tbit-air-pkg/obs-to-webhook` (중앙 계정)
- 트리거: OBS Object Created on `tbit-air` → 분석서버 `/webhook/alarm` POST
- 객체 키 첫 세그먼트를 `resourceName`으로 사용(멀티테넌트), 폴더 마커 skip
- **외부 webhook 호출이라 NAT 필요** → 중앙 VPC에 NAT 구축함

**고객 CF** — `tbit-air-cust-pkg/collect-export` (개인 계정)
- CI `event/search` 폴링(또는 `data/query` 메트릭 직접 조회) → 임계 초과 시 CLA `SearchLogs` → `boto3 PUT` to `tbit-air`
- **공인 OBS 엔드포인트 도달에 NAT 필요** → 개인 VPC에도 NAT 구축
- `force:true`로 업로드 경로 end-to-end 검증 완료 (실제 CLA 로그 4줄 수집·업로드·중앙 분석까지)

## 3. NCP 특이사항 (이번에 학습한 것 — 재현 시 필수)

- **NCP CF 외부 egress**: Ncloud API(`*.apigw.ntruss.com`)는 **NAT 없이 도달**. 임의 외부 URL(우리 webhook)·**공인 OBS(`ncloudstorage.com`)는 NAT 필요**. → VPC 연결 + 공인 NAT GW.
- **OBS 프라이빗 엔드포인트** `kr.object.private.ncpstorage.com`: VPC 내부에서 도달되나 **계정 범위 한정** — cross-account는 `InvalidAccessKeyId`로 실패. **cross-account는 공인 엔드포인트 + NAT만 검증됨.**
- **cross-account 업로드**: 업로더(고객)가 올린 객체는 고객 소유 → 버킷주인(중앙)이 못 읽음. **`GrantFullControl='id="ncp-2617879-0"'` 필수.** S3 canned `bucket-owner-full-control`은 NCP 미지원("Invalid canned ACL").
- **boto3 체크섬**: boto3 1.36+ 기본 streaming 체크섬을 NCP OBS가 거부(`...SHA256_Header_Mismatch`). `Config(request_checksum_calculation="when_required", response_checksum_validation="when_required")`로 끔.
- **NCP CF 런타임**: python:3.13에 `boto3`(1.37.7)·`requests` 있음. 단 **`import boto3`를 모듈 최상단에 두면 init 타임아웃("Cannot start action")** → **함수 안에서 import**.
- **NCP NAT**: 용도=`NatGateway` 전용 서브넷 별도 필요 + 라우트 `0.0.0.0/0 → NATGW`. 공인/사설 NAT 중 인터넷 egress는 **공인**.
- **CLA SearchLogs**: `POST /api/kr-v1/logs/search`, 타임스탬프 **ms**. 응답 `result.searchResult[]` (logTime/logType/logDetail).
- **CI 메트릭 직접 조회**: `GetSystemSchemaKeyList`(GET `/cw_fea/real/cw/api/schema/system/list` → `prodName=="System/Server(VPC)"`의 `cw_key`) + `QueryData`(POST `/cw_fea/real/cw/api/data/query`, body: cw_key/productName/metric=`avg_cpu_used_rto`/interval=`Min1`/aggregation=`AVG`/dimensions={instanceNo}, 응답 `[[ts,값],…]` 값 0~1).

## 4. 왜 방향을 바꾸나

| 문제 | 내용 |
|------|------|
| **비용** | 고객마다 NAT GW(시간당 과금) + CLA 인제스트 + CF → 고객 수에 비례 폭증 |
| **NCP 종속** | Cloud Insight·CLA·CF·OBS 전부 NCP 전용 → 비-NCP(AWS·온프렘) 고객은 수집·트리거 자체 불가 |

## 5. 새 방향 — 에이전트 기반 (vendor-neutral)

> **핵심: "OBS 업로드 → (기존) OBS 트리거 → 분석 자동시작" 체인은 그대로 유지한다.**
> 에이전트/관측 스택은 앞단(수집·알람)만 교체하고, 뒷단(OBS→CF→webhook→분석)은 이미 검증된 그대로 재사용한다.

```
[각 서버] Grafana Alloy → metrics(VictoriaMetrics) + logs(VictoriaLogs)   (서버 아웃바운드만 — NAT/CF 불필요)

[중앙] vmalert(CPU>80% 5분) 발화
   → 브리지(vmalert webhook 수신): VictoriaLogs에서 해당 host 직전 5분 로그 쿼리
   → 로그 묶음을 중앙 OBS(tbit-air, key=<resource>/CLA-…/result.json)에 업로드
   ───────────────── 여기서부터 기존 그대로 ─────────────────
   → OBS Object Created → 중앙 CF(obs-to-webhook) → /webhook/alarm
   → 서버가 OBS 다운로드 → claude 분석 → 대시보드 + 이메일
```

**교체 매핑**

| 지금 (NCP) | 새 방식 |
|-----------|--------|
| Cloud Insight(메트릭) | VictoriaMetrics + Alloy |
| CLA(로그) | VictoriaLogs + Alloy |
| CI 알람 + 고객 CF 폴링/업로드 | vmalert 룰 + **중앙 브리지**(vmalert→VictoriaLogs→OBS) |
| 고객측 NAT/CF/CLA/Cloud Insight | **전부 제거** — 고객측은 **Alloy 1개만** |

**유지(KEEP — 변경 없음)**: 중앙 OBS + 중앙 CF(`obs-to-webhook`) + `/webhook/alarm` + OBS 다운로드 + 분석 + 대시보드/이메일. **"OBS 업로드 → 분석 자동시작" 트리거 체인 그대로.**
**신규(NEW)**: Alloy + VictoriaMetrics/Logs + vmalert + **중앙 브리지**(알람→VictoriaLogs 쿼리→중앙 OBS 업로드).
**제거(REMOVE)**: 고객측 NAT/CF/CLA/Cloud Insight.

**장점**: 비용 고정비화(고객당 곱하기 X), 멀티클라우드(NCP/AWS/온프렘 어디든), **검증된 분석 백엔드(OBS→CF→분석) 무변경 재사용**.
**트레이드오프**: 중앙 관측 스택(VM/VictoriaLogs/vmalert/브리지) 직접 운영 — 단 고객 늘수록 유리한 고정비.

## 6. 서버측 코드 변경 — 거의 없음

로그가 **여전히 OBS로 도착**하므로 `obs_collector`·webhook·분석 골격 **변경 불필요**.
새로 만드는 건 중앙 **브리지** 하나:

| 신규 컴포넌트 | 역할 |
|--------------|------|
| **브리지** (중앙 계정/서버에서 실행) | vmalert webhook 수신 → VictoriaLogs LogsQL로 host 직전 5분 쿼리 → `obs_collector`가 기대하는 **JSONL(`@timestamp`/`type`/`message`)** 로 패키징 → 중앙 OBS(`tbit-air`)에 업로드 |

- 브리지는 **중앙 계정 OBS에 같은 계정으로 업로드** → cross-account grant·NAT·`InvalidAccessKeyId` 이슈 **없음**. (boto3 PUT 시 NCP 체크섬 Config만 주의: `request_checksum_calculation="when_required"`)
- 객체 키는 `<resource_name>/…` 유지 → 기존 중앙 CF가 prefix로 사이트 매칭 (무변경).
- 결과적으로 **앱(FastAPI) 코드 0~최소 수정**. 작업 대부분은 관측 스택 + Alloy + 브리지(신규 작은 서비스).

## 7. 결정사항 (2026-06-02 확정 — PoC 속도 우선)

1. 관측 스택 위치 → **중앙 서버(tbit-air-pub) 기존 compose에 합류**. (부하 커지면 별도 인스턴스로 분리)
2. 로그 저장소 → **VictoriaLogs** (VM과 동계열, LogsQL로 직전 5분 쿼리 깔끔).
3. 알림 경로 → **vmalert가 직접 브리지 webhook 호출** (Alertmanager 미도입; dedup/silence 필요 시 후속 추가).
4. 수집 에이전트 → **Grafana Alloy** (메트릭 remote_write + 로그 push 단일 에이전트).

## 8. 다음 PoC 단계

1. 중앙 compose에 **VictoriaMetrics + VictoriaLogs + vmalert** 추가
2. 테스트 서버에 **Alloy 설치** → 메트릭 remote_write + 로그 push (중앙으로, 인증/TLS)
3. **`CPU>80% 5분` vmalert 룰** → **브리지**로 webhook
4. **브리지** 작성: VictoriaLogs 쿼리(host 직전 5분) → JSONL 패키징 → **중앙 OBS 업로드**
   → 그 뒤는 **기존 OBS Object Created → CF → /webhook → 분석 자동시작 (무변경)**
5. end-to-end 검증 → 이게 **모든 고객 템플릿**(서버에 Alloy만 설치)이 됨
6. (앱 코드는 거의 안 건드림 — 4번 브리지가 핵심 신규 작업)

## 9. 정리(cleanup) TODO — 현재 빌드 잔여물

- `tbit-air` 버킷의 테스트 객체 삭제: `test*.json`, `probe-*`, `nat-test-*`, `tbit-air-mon-01/CLA-* `(테스트분), `acltest-*`, `grant-*`/`public-*`/`grant-read-*`
- DB의 테스트 incident 정리
- 하드닝(보류): DB 암호화(`ENCRYPTION_KEY`), OBS grant 업로드-only+prefix 스코프, admin 비번(`admin1234`) 변경
- WMS: 고객 서버용 시나리오 미생성 (NCP-native 경로 유지 시에만 필요)

## 10. 참고 자료

- 운영 접속/배포: 메모리 `reference_production`
- cross-account grant 상세: 메모리 `obs-crossaccount-upload-grant`
- 현재 상태 요약: 메모리 `project_status`
- 방향 전환 결정: 메모리 `agent-based-collection-pivot`
- NCP-native CF 흐름 원형: `cloud-functions/`, `docs/cf-setup.md`

---

## 11. 대전환 (2026-06-04) — monitoring_msp 통합 (§5~9 일부 대체)

별도 레포 `dev/monitoring_msp`가 **이미 메트릭/알람/에이전트 스택을 가동 중**임을 발견. 이걸 재사용하고, ai-incident-response는 AI 분석만 얹는다.

**monitoring_msp 현황(재사용 대상)**
- Alloy 에이전트 **direct/relay 모드**, **9999 push**, 아웃바운드 차단 서버까지 커버
- 중앙: VictoriaMetrics + vmalert + **Alertmanager** + Grafana + Gmail 이메일
- 라벨: customer_id / server_name / csp / region / environment
- **로그는 수집하지 않음 → 중앙엔 메트릭만 적재** (= "중앙에 앱 로그 안 쌓는다" 모델과 일치)

**바뀐 결정**
- ai-incident-response의 **VictoriaMetrics·vmalert·VictoriaLogs는 중복 → 폐기.** monitoring_msp 재사용.
- **중앙 로그 저장 안 함**(VictoriaLogs 제외). 로그는 **트리거 때만** 수집, 수명주기는 **OBS**가 관리.
- ai-incident-response가 더하는 유일한 것 = **"알람 시 로그 수집 + AI 분석"**.

**통합 흐름**
```
monitoring_msp Alertmanager (임계 초과 발화)
   → webhook → ai-incident-response 브리지
   → 해당 host 직전 5분 로그 수집  ← (NEW, 미해결: 수집 방식)
   → 중앙 OBS(tbit-air) 업로드
   ──────── 여기서부터 기존 그대로(무변경) ────────
   → OBS Object Created → CF(obs-to-webhook) → /webhook/alarm → claude 분석 → 대시보드/이메일
```

**미해결 (다음 단계)** — 에이전트가 평소 로그를 안 모으므로 알람 시 5분 로그를 호스트에서 꺼내는 방식:
- **direct 서버**: 호스트가 자가 수집(`journalctl --since=-5min`) → OBS 직접 업로드(아웃바운드). → **direct-first PoC 권장**
- **relay(아웃바운드 차단) 서버**: 호스트가 OBS 도달 불가 → relay 경유 전송 필요(후속).

**`monitoring/` 스캐폴드 처리**: 브리지만 존속(입력=Alertmanager webhook, 로그 원천=호스트 수집으로 변경). docker-compose.poc.yml의 VM/VictoriaLogs/vmalert 및 alloy 로그 push 부분은 정리 대상.

---

## 12. 최종 확정 아키텍처 (2026-06-04) — §5~11 종합·대체

여러 차례 논의 끝에 확정. 다이어그램: [`architecture.drawio`](architecture.drawio).

### 핵심 결정
- **OBS · CF(`obs-to-webhook`) · 별도 브리지 전부 제거.** 이게 마지막 NCP 전용 조각이었음 → 제거로 **NCP 종속 완전 소거**(vendor-neutral 목표 완성). "앱 무변경" 원칙은 포기하고 **앱에 코드 추가**를 택함(가치 교환 수용).
- **모드 A (기본·상용 표준):** Alloy가 메트릭+로그 **상시 push** → 중앙 VictoriaLogs(**7일 롤링** 보관). 알람 시 **분석서버가 직접 VictoriaLogs를 직전 5분 쿼리(pull)**. 호스트 추가 코드 0, 검색·대시보드 덤.
- **모드 B (데이터 거주성 제약 고객 예외):** 로그 중앙 미전송. 호스트 **폴러**가 알람 시 `journalctl 5분` → 분석서버로 **inline POST**(아웃바운드, 인바운드 안 엶).
- **폐쇄망(relay):** relay-server가 로그까지 중계 → 모드 A가 자연스럽게 커버(스트리밍 모델). 모드 B over relay는 HTTP 프록시 필요(후속).
- **배포: 2서버 / 1 사설 subnet.** [서버1] monitoring_msp 중앙(VictoriaMetrics·VictoriaLogs·vmalert·Alertmanager·Grafana) ↔ [서버2] ai-incident-response 분석. **VictoriaLogs/Metrics는 인증이 없으므로 절대 공인 노출 금지 → 사설 subnet 필수.** 분석서버↔VL 쿼리, Alertmanager→분석서버 webhook 모두 사설 hop. 공인 노출은 에이전트 ingress(monitoring_msp 기존) + 분석 대시보드뿐.

### 최종 흐름
```
[고객 호스트] Alloy ──상시 push(메트릭+로그)──► 공인 Ingress(monitoring_msp, relay로 폐쇄망도)
   ┌──────────── 사설 subnet ────────────┐
   │ [서버1] VictoriaMetrics / VictoriaLogs(7d) / vmalert / Alertmanager / Grafana │
   │     vmalert 발화 → Alertmanager ──① webhook(사설)──► [서버2] /webhook/alert     │
   │ [서버2] /webhook/alert → ② VictoriaLogs 직전5분 쿼리(pull,사설) → ③ claude 분석 │
   │         → incident DB → 대시보드 + 이메일                                         │
   └──────────────────────────────────────┘
   (모드 B: 폴러가 journalctl 5분을 /webhook/alert로 직접 POST)
```

### 코드 작업 (다음)
- **신규(서버2 앱)**: `POST /webhook/alert`(Alertmanager 포맷 파싱) + `victorialogs_collector`(host·시각으로 VictoriaLogs 쿼리, 기존 `obs_collector` 자리 대체, 동일 `list[str]` 반환). analyzer·DB·대시보드·이메일은 **그대로**.
- **레거시화**: `obs_collector`, `cloud-functions/`, `/webhook/alarm`(OBS 경로).
- **`monitoring/` 스캐폴드 정리**: VictoriaLogs(존속) 외 bridge·OBS·vmalert(monitoring_msp 것 사용)·docker-compose의 중복 서비스 제거 방향. Alloy config(메트릭+로그)는 존속.
- **PoC = 모드 A e2e** (로컬은 same docker network로 사설 subnet 흉내).

---

## 부록 A — 검증된 고객 CF 코드 (NCP-native, 레거시 참고용)

`tbit-air-cust-pkg/collect-export` (개인 계정, VPC=NAT경로 private 서브넷, 디폴트 파라미터에 고객 키).
메트릭 직접 조회판은 `GetSystemSchemaKeyList`+`QueryData` 사용. 핵심 교정점:
- import는 함수 안에서 (boto3 init 타임아웃 회피)
- `boto3.client(endpoint_url="https://kr.object.ncloudstorage.com", config=Config(request_checksum_calculation="when_required", response_checksum_validation="when_required"))`
- `put_object(..., GrantFullControl='id="ncp-2617879-0"')`
- 객체 키 `{resource_name}/CLA-{ts}/result.json` (중앙 CF가 prefix로 사이트 매칭)
- 전체 코드는 이 세션 트랜스크립트 참조(에이전트 전환으로 레거시화 예정).
