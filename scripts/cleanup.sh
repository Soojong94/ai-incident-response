#!/usr/bin/env bash
# 중앙 서버 테스트 데이터 정리.
# 사용: /opt/ai-incident-response 에서  ./scripts/cleanup.sh
# (app 컨테이너 안에서 직접 DB를 비우므로 로그인 불필요)
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[1] 장애 내역 + AI 메모 전체 삭제"
docker compose exec -T app python -c "from src.db.database import SessionLocal; from src.db.crud import delete_all_incidents; db=SessionLocal(); print('  삭제된 장애:', delete_all_incidents(db), '건 (+연동 AI 메모)'); db.close()"

# ── 선택: 자동 생성된 사이트도 삭제 (주석 해제) ───────────────────────────────
# echo "[2] 자동 생성 사이트 삭제"
# docker compose exec -T app python -c "from src.db.database import SessionLocal; from src.db.models import Site; db=SessionLocal(); q=db.query(Site).filter(Site.auto_created==True); n=q.count(); q.delete(synchronize_session=False); db.commit(); db.close(); print('  삭제된 자동사이트:', n)"

# ── 선택: VictoriaMetrics 테스트 메트릭 삭제 (host 지정 — 운영 메트릭 주의!) ──
# for h in win-test-01 ubuntu-test rocky-test relay-agent-test demo-01; do
#   curl -s "http://localhost:8428/api/v1/admin/tsdb/delete_series" --data-urlencode "match[]={host=\"$h\"}" && echo "  VM 삭제: $h"
# done
# (VictoriaLogs는 retention(7일)으로 자동 정리. 즉시 비우려면 볼륨 재생성:
#   docker compose stop victorialogs && docker volume rm ai-incident-response_? ; 또는 data/vldata 비우기)

echo "완료."
