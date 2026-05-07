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
from src.db.crud import create_incident, get_incident, get_incidents
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
    return templates.TemplateResponse("demo.html", {"request": request})


@app.post("/test/trigger", status_code=202)
async def test_trigger(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    metric_type: str = "cpu",
    resource_name: str = "server-prod-01",
    current_value: float = 95.3,
    threshold_value: float = 85.0,
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
    incident = create_incident(db, payload)
    background_tasks.add_task(run_pipeline, incident.id, payload)
    return {"status": "accepted", "incident_id": incident.id}


# ── Dashboard ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    incidents = get_incidents(db, limit=50)
    return templates.TemplateResponse("index.html", {"request": request, "incidents": incidents})


@app.get("/incidents/{incident_id}", response_class=HTMLResponse)
def incident_detail(incident_id: int, request: Request, db: Session = Depends(get_db)):
    incident = get_incident(db, incident_id)
    if not incident:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Incident not found")
    return templates.TemplateResponse("detail.html", {"request": request, "incident": incident})


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
