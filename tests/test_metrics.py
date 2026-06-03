# PROMPT: Create pytest unit tests for the FastAPI Store Intelligence metrics and funnel endpoints using a mock SQLite database. Test coverage should include empty store traffic, basic visits, POS conversion rate correlation, queue depth tracking, and double-entry session deduplication.
# CHANGES MADE: Switched test database from in-memory to a file-based temporary test database in the data folder to allow multi-connection table sharing. Configured setup and teardown fixtures to delete the file before and after tests.

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

TEST_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "test_store_intelligence.db")
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
        # Try to delete after tests
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

def test_empty_store_metrics(client):
    """
    Tests that querying an empty store returns zero counts instead of crashing.
    """
    response = client.get("/stores/STORE_EMPTY/metrics")
    assert response.status_code == 200
    data = response.json()
    assert data["store_id"] == "STORE_EMPTY"
    assert data["unique_visitors"] == 0
    assert data["conversion_rate"] == 0.0
    assert data["current_queue_depth"] == 0

def test_metrics_calculation(client, test_db):
    """
    Tests metrics logic (visitors, conversion, dwells) using structured events and matched POS txns.
    """
    store_id = "ST1008"
    v1 = "VIS_CUST01"
    v2 = "VIS_CUST02"
    staff = "VIS_STAFF01"
    
    # 1. Populate mock POS transaction
    tx = POSTransactionDB(
        transaction_id="TX_1001",
        store_id=store_id,
        timestamp=datetime(2026, 4, 10, 20, 15, 0),
        basket_value=500.00
    )
    test_db.add(tx)
    
    # 2. Add events
    events = [
        # Customer 1 visits skincare zone, goes to billing, completes transaction
        StoreEventDB(event_id="e1", store_id=store_id, camera_id="CAM_3", visitor_id=v1, event_type="ENTRY", timestamp=datetime(2026, 4, 10, 20, 10, 0), is_staff=False),
        StoreEventDB(event_id="e2", store_id=store_id, camera_id="CAM_1", visitor_id=v1, event_type="ZONE_ENTER", zone_id="SKINCARE", timestamp=datetime(2026, 4, 10, 20, 11, 0), is_staff=False),
        StoreEventDB(event_id="e3", store_id=store_id, camera_id="CAM_1", visitor_id=v1, event_type="ZONE_EXIT", zone_id="SKINCARE", dwell_ms=60000, timestamp=datetime(2026, 4, 10, 20, 12, 0), is_staff=False),
        StoreEventDB(event_id="e4", store_id=store_id, camera_id="CAM_5", visitor_id=v1, event_type="ZONE_ENTER", zone_id="BILLING", timestamp=datetime(2026, 4, 10, 20, 12, 30), is_staff=False),
        
        # Customer 2 visits cosmetics zone, stays in billing queue but abandons
        StoreEventDB(event_id="e5", store_id=store_id, camera_id="CAM_3", visitor_id=v2, event_type="ENTRY", timestamp=datetime(2026, 4, 10, 20, 3, 0), is_staff=False),
        StoreEventDB(event_id="e6", store_id=store_id, camera_id="CAM_2", visitor_id=v2, event_type="ZONE_ENTER", zone_id="COSMETICS", timestamp=datetime(2026, 4, 10, 20, 3, 30), is_staff=False),
        StoreEventDB(event_id="e7", store_id=store_id, camera_id="CAM_5", visitor_id=v2, event_type="ZONE_ENTER", zone_id="BILLING", timestamp=datetime(2026, 4, 10, 20, 4, 0), is_staff=False),

        # Staff member moves around - must be excluded from customer metrics
        StoreEventDB(event_id="e8", store_id=store_id, camera_id="CAM_4", visitor_id=staff, event_type="ENTRY", timestamp=datetime(2026, 4, 10, 20, 9, 0), is_staff=True),
        StoreEventDB(event_id="e9", store_id=store_id, camera_id="CAM_4", visitor_id=staff, event_type="ZONE_ENTER", zone_id="BACKROOM", timestamp=datetime(2026, 4, 10, 20, 9, 30), is_staff=True)
    ]
    for e in events:
        test_db.add(e)
    test_db.commit()
    
    # Run tests
    response = client.get(f"/stores/{store_id}/metrics")
    assert response.status_code == 200
    data = response.json()
    
    assert data["unique_visitors"] == 2
    assert data["conversion_rate"] == 0.5
    assert data["avg_dwell_by_zone"]["SKINCARE"] == 60000
    assert data["current_queue_depth"] == 1
    assert data["abandonment_rate"] == 0.5

def test_funnel_response(client, test_db):
    """
    Verifies that funnel aggregates correct unique counts and calculates drop-offs.
    """
    store_id = "ST1008"
    v1 = "VIS_CUST01"
    
    tx = POSTransactionDB(
        transaction_id="TX_1001",
        store_id=store_id,
        timestamp=datetime(2026, 4, 10, 20, 15, 0),
        basket_value=500.00
    )
    test_db.add(tx)
    
    events = [
        StoreEventDB(event_id="e1", store_id=store_id, camera_id="CAM_3", visitor_id=v1, event_type="ENTRY", timestamp=datetime(2026, 4, 10, 20, 10, 0), is_staff=False),
        StoreEventDB(event_id="e2", store_id=store_id, camera_id="CAM_1", visitor_id=v1, event_type="ZONE_ENTER", zone_id="SKINCARE", timestamp=datetime(2026, 4, 10, 20, 11, 0), is_staff=False),
        StoreEventDB(event_id="e4", store_id=store_id, camera_id="CAM_5", visitor_id=v1, event_type="ZONE_ENTER", zone_id="BILLING", timestamp=datetime(2026, 4, 10, 20, 12, 30), is_staff=False)
    ]
    for e in events:
        test_db.add(e)
    test_db.commit()
    
    response = client.get(f"/stores/{store_id}/funnel")
    assert response.status_code == 200
    data = response.json()
    
    assert data["funnel"]["Entry"]["count"] == 1
    assert data["funnel"]["Zone_Visit"]["count"] == 1
    assert data["funnel"]["Billing_Queue"]["count"] == 1
    assert data["funnel"]["Purchase"]["count"] == 1
    
    assert data["funnel"]["Zone_Visit"]["drop_off_pct"] == 0.0
    assert data["funnel"]["Billing_Queue"]["drop_off_pct"] == 0.0
    assert data["funnel"]["Purchase"]["drop_off_pct"] == 0.0
