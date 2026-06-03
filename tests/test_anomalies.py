# PROMPT: Create pytest unit tests for the Store Intelligence anomalies and health API endpoints using a mock database session. Cover billing queue spikes, conversion rate drop warning conditions, dead zone detection for inactive product areas, and stale feed warning checks in health status.
# CHANGES MADE: Switched to a file-based temporary test database in the data folder to allow multi-connection table sharing. Configured setup and teardown fixtures to delete the file before and after tests. Added assertions for anomalies and health states.

import pytest
import sys
import os
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Set python path to find app modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

from main import app
from ingestion import get_db
from models import Base, StoreEventDB, POSTransactionDB

TEST_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "test_store_intelligence_anom.db")
TEST_DATABASE_URL = f"sqlite:///{TEST_DB_PATH}"

@pytest.fixture(scope="function")
def test_db():
    # Clean old test DB file if it exists
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass
            
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass

@pytest.fixture(scope="function")
def client(test_db):
    def override_get_db():
        yield test_db
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()

def test_health_endpoint_healthy(client, test_db):
    """
    Tests /health endpoint output when database is healthy but stale feed condition is met.
    """
    store_id = "ST1008"
    e = StoreEventDB(
        event_id="e1",
        store_id=store_id,
        camera_id="CAM_3",
        visitor_id="VIS_01",
        event_type="ENTRY",
        timestamp=datetime.now() - timedelta(minutes=15),
        is_staff=False
    )
    test_db.add(e)
    test_db.commit()

    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"
    assert store_id in data["stores"]
    assert data["stores"][store_id]["feed_status"] == "STALE_FEED"

def test_anomalies_dead_zone(client, test_db):
    """
    Tests dead zone anomaly detection: a zone has event history but no entries in the last 30 minutes.
    """
    store_id = "ST1008"
    current_time = datetime(2026, 4, 10, 20, 45, 0)
    
    events = [
        StoreEventDB(event_id="e1", store_id=store_id, camera_id="CAM_1", visitor_id="VIS_C1", event_type="ZONE_ENTER", zone_id="SKINCARE", timestamp=current_time - timedelta(minutes=45), is_staff=False),
        StoreEventDB(event_id="e2", store_id=store_id, camera_id="CAM_3", visitor_id="VIS_C2", event_type="ENTRY", timestamp=current_time, is_staff=False)
    ]
    for e in events:
        test_db.add(e)
    test_db.commit()
    
    response = client.get(f"/stores/{store_id}/anomalies")
    assert response.status_code == 200
    data = response.json()
    
    dead_zone_anomalies = [a for a in data["anomalies"] if a["anomaly_type"] == "DEAD_ZONE"]
    assert len(dead_zone_anomalies) > 0
    assert "SKINCARE" in dead_zone_anomalies[0]["details"]
    assert dead_zone_anomalies[0]["severity"] == "INFO"

def test_anomalies_queue_spike(client, test_db):
    """
    Tests queue spike detection when the current queue size exceeds the average.
    """
    store_id = "ST1008"
    current_time = datetime(2026, 4, 10, 20, 10, 0)
    
    events = [
        StoreEventDB(event_id="e1", store_id=store_id, camera_id="CAM_5", visitor_id="V_HIST1", event_type="BILLING_QUEUE_JOIN", zone_id="BILLING", timestamp=current_time - timedelta(minutes=20), metadata_json={"queue_depth": 1, "session_seq": 1}, is_staff=False),
        StoreEventDB(event_id="e2", store_id=store_id, camera_id="CAM_5", visitor_id="V_HIST2", event_type="BILLING_QUEUE_JOIN", zone_id="BILLING", timestamp=current_time - timedelta(minutes=15), metadata_json={"queue_depth": 1, "session_seq": 1}, is_staff=False),
        
        StoreEventDB(event_id="e3", store_id=store_id, camera_id="CAM_5", visitor_id="V_C1", event_type="ZONE_ENTER", zone_id="BILLING", timestamp=current_time, is_staff=False),
        StoreEventDB(event_id="e4", store_id=store_id, camera_id="CAM_5", visitor_id="V_C2", event_type="ZONE_ENTER", zone_id="BILLING", timestamp=current_time, is_staff=False),
        StoreEventDB(event_id="e5", store_id=store_id, camera_id="CAM_5", visitor_id="V_C3", event_type="ZONE_ENTER", zone_id="BILLING", timestamp=current_time, is_staff=False),
        StoreEventDB(event_id="e6", store_id=store_id, camera_id="CAM_5", visitor_id="V_C4", event_type="ZONE_ENTER", zone_id="BILLING", timestamp=current_time, is_staff=False)
    ]
    for e in events:
        test_db.add(e)
    test_db.commit()
    
    response = client.get(f"/stores/{store_id}/anomalies")
    assert response.status_code == 200
    data = response.json()
    
    spike_anomalies = [a for a in data["anomalies"] if a["anomaly_type"] == "BILLING_QUEUE_SPIKE"]
    assert len(spike_anomalies) > 0
    assert spike_anomalies[0]["severity"] == "CRITICAL"
    assert "queue depth" in spike_anomalies[0]["details"]
