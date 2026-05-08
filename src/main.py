import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from src.db.database import init_db, get_db
from src.db.crud import create_incident, get_incident, get_incidents, delete_incident, delete_all_incidents
from src.webhook.handler import receive_alarm, run_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("DB initialized")
    from src.collector.ncp_poller import poller
    await poller.start()
    yield
    poller.stop()


app = FastAPI(title="AI Incident Response", lifespan=lifespan)
templates = Jinja2Templates(directory="src/ui/templates")


# ── Webhook ──────────────────────────────────────────────────────────────────

@app.post("/webhook/alarm", status_code=202)
async def webhook_alarm(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    payload = await request.json()
    result = await receive_alarm(request, payload, db)
    incident_id = result["incident_id"]
    background_tasks.add_task(run_pipeline, incident_id, payload)
    return {"status": "accepted", "incident_id": incident_id}


# ── Dev test trigger ─────────────────────────────────────────────────────────

@app.get("/test/demo", response_class=HTMLResponse)
def demo_page(request: Request):
    return templates.TemplateResponse(request, "demo.html")


@app.post("/test/trigger", status_code=202)
async def test_trigger(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    metric_type: str = "cpu",
    resource_name: str = "team1-test-server",
    current_value: float = 95.3,
    threshold_value: float = 85.0,
    obs_object_key: str = "",
    obs_bucket: str = "",
):
    payload = {
        "alarmName": f"{metric_type.upper()}-High-Alert",
        "alarmId": uuid.uuid4().hex[:12],
        "resourceName": resource_name,
        "metricType": metric_type,
        "threshold": threshold_value,
        "currentValue": current_value,
        "alarmTime": datetime.now().isoformat(),
    }
    if obs_object_key:
        payload["obs_object_key"] = obs_object_key
        payload["obs_bucket"] = obs_bucket or "team1-demo"
    incident = create_incident(db, payload)
    background_tasks.add_task(run_pipeline, incident.id, payload)
    return {"status": "accepted", "incident_id": incident.id}


# ── OBS 원본 로그 다운로드 ───────────────────────────────────────────────────────

@app.get("/api/incidents/{incident_id}/logs/raw")
async def api_logs_raw(incident_id: int, db: Session = Depends(get_db)):
    from fastapi.responses import StreamingResponse, JSONResponse
    incident = get_incident(db, incident_id)
    if not incident:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Incident not found")
    if not incident.obs_object_key:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="No OBS file for this incident")
    from src.collector.obs_collector import _s3_client
    s3 = _s3_client()
    resp = s3.get_object(Bucket=incident.obs_bucket, Key=incident.obs_object_key)
    body = resp["Body"].read()
    filename = incident.obs_object_key.split("/")[-1]
    return StreamingResponse(
        iter([body]),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Incident 삭제 ─────────────────────────────────────────────────────────────

@app.delete("/api/incidents/{incident_id}", status_code=200)
def api_delete_incident(incident_id: int, db: Session = Depends(get_db)):
    if not delete_incident(db, incident_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"deleted": incident_id}


@app.delete("/api/incidents", status_code=200)
def api_delete_all_incidents(db: Session = Depends(get_db)):
    count = delete_all_incidents(db)
    return {"deleted": count}


# ── Dashboard ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    incidents = get_incidents(db, limit=50)
    return templates.TemplateResponse(request, "index.html", {"incidents": incidents})


@app.get("/incidents/{incident_id}", response_class=HTMLResponse)
def incident_detail(incident_id: int, request: Request, db: Session = Depends(get_db)):
    incident = get_incident(db, incident_id)
    if not incident:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Incident not found")
    return templates.TemplateResponse(request, "detail.html", {"incident": incident})


# ── API (JSON) ────────────────────────────────────────────────────────────────

@app.get("/api/incidents")
def api_incidents(db: Session = Depends(get_db)) -> list[Any]:
    incidents = get_incidents(db, limit=50)
    result = []
    for inc in incidents:
        result.append({
            "id": inc.id,
            "alarm_name": inc.alarm_name,
            "resource_name": inc.resource_name,
            "metric_type": inc.metric_type,
            "current_value": inc.current_value,
            "alarm_time": inc.alarm_time.isoformat() if inc.alarm_time else None,
            "status": inc.status,
            "severity": inc.severity,
            "created_at": inc.created_at.isoformat() if inc.created_at else None,
        })
    return result


@app.get("/api/incidents/{incident_id}")
def api_incident_detail(incident_id: int, db: Session = Depends(get_db)) -> Any:
    incident = get_incident(db, incident_id)
    if not incident:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Incident not found")
    analysis = incident.analysis_result
    return {
        "id": incident.id,
        "alarm_name": incident.alarm_name,
        "resource_name": incident.resource_name,
        "metric_type": incident.metric_type,
        "threshold_value": incident.threshold_value,
        "current_value": incident.current_value,
        "alarm_time": incident.alarm_time.isoformat() if incident.alarm_time else None,
        "status": incident.status,
        "severity": incident.severity,
        "obs_file_url": (
            f"https://kr.object.ncloudstorage.com/{incident.obs_bucket}/{incident.obs_object_key}"
            if incident.obs_object_key else None
        ),
        "logs": [{"content": l.log_content, "source": l.source} for l in incident.logs],
        "analysis": {
            "cause_category": analysis.cause_category,
            "cause_detail": analysis.cause_detail,
            "severity": analysis.severity,
            "impact_scope": analysis.impact_scope,
            "immediate_actions": analysis.immediate_actions,
            "prevention": analysis.prevention,
            "confidence": analysis.confidence,
        } if analysis else None,
    }
