"""Webhook 수신 및 인시던트 생성 기본 테스트."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.database import Base, get_db
from src.main import app

TEST_DB_URL = "sqlite:///./test_incidents.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()


def test_test_trigger_returns_202(client):
    resp = client.post("/test/trigger", params={"metric_type": "cpu", "resource_name": "web-01", "current_value": 95})
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "accepted"
    assert "incident_id" in data


def test_incidents_list(client):
    client.post("/test/trigger", params={"metric_type": "memory", "resource_name": "db-01"})
    resp = client.get("/api/incidents")
    assert resp.status_code == 200
    incidents = resp.json()
    assert len(incidents) >= 1
    assert incidents[0]["metric_type"] == "memory"


def test_incident_detail_not_found(client):
    resp = client.get("/api/incidents/99999")
    assert resp.status_code == 404


def test_webhook_alarm(client):
    payload = {
        "alarmName": "CPU-High",
        "alarmId": "test-alarm-001",
        "resourceName": "server-01",
        "metricType": "cpu",
        "threshold": 85,
        "currentValue": 92,
        "alarmTime": "2026-05-07T14:00:00+09:00",
    }
    resp = client.post("/webhook/alarm", json=payload)
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "accepted"
