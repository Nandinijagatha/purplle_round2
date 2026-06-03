from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from typing import Dict, Any

from ingestion import get_db
from models import StoreEventDB, POSTransactionDB

router = APIRouter()

@router.get("/stores/{store_id}/metrics")
async def get_store_metrics(store_id: str, db: Session = Depends(get_db)):
    """
    Computes real-time store metrics (unique visitors, conversion rate,
    average zone dwells, current queue depth, and abandonment rate).
    Excludes is_staff=True events.
    """
    # Verify store existence (must have events or transactions)
    event_exists = db.query(StoreEventDB).filter(StoreEventDB.store_id == store_id).first()
    txn_exists = db.query(POSTransactionDB).filter(POSTransactionDB.store_id == store_id).first()
    
    if not event_exists and not txn_exists:
        # Handle empty/zero-traffic store correctly instead of crashing
        return {
            "store_id": store_id,
            "unique_visitors": 0,
            "conversion_rate": 0.0,
            "avg_dwell_by_zone": {},
            "current_queue_depth": 0,
            "abandonment_rate": 0.0
        }

    # Fetch all non-staff events for the store
    events = db.query(StoreEventDB).filter(
        StoreEventDB.store_id == store_id,
        StoreEventDB.is_staff == False
    ).order_by(StoreEventDB.timestamp).all()

    # Fetch all POS transactions for the store
    transactions = db.query(POSTransactionDB).filter(
        POSTransactionDB.store_id == store_id
    ).all()

    if not events:
        return {
            "store_id": store_id,
            "unique_visitors": 0,
            "conversion_rate": 0.0,
            "avg_dwell_by_zone": {},
            "current_queue_depth": 0,
            "abandonment_rate": 0.0
        }

    # 1. Unique Visitors (excluding staff)
    unique_visitors = list(set(e.visitor_id for e in events))
    num_unique_visitors = len(unique_visitors)

    # 2. Conversion Rate
    # A visitor is converted if they were in the billing zone in the 5-minute window BEFORE a transaction timestamp
    converted_visitors = set()
    for txn in transactions:
        txn_time = txn.timestamp
        window_start = txn_time - timedelta(minutes=5)
        
        # Find any non-staff visitor in the BILLING zone during this window
        billing_events = [
            e.visitor_id for e in events
            if e.zone_id == "BILLING" and window_start <= e.timestamp <= txn_time
        ]
        for v_id in billing_events:
            converted_visitors.add(v_id)

    conversion_rate = 0.0
    if num_unique_visitors > 0:
        conversion_rate = len(converted_visitors) / num_unique_visitors

    # 3. Average Dwell Time per Zone
    # Aggregate dwell_ms from ZONE_EXIT or ZONE_DWELL events where zone_id is present
    dwells = {} # zone_id -> list of dwells
    for e in events:
        if e.zone_id and e.dwell_ms > 0:
            if e.zone_id not in dwells:
                dwells[e.zone_id] = []
            dwells[e.zone_id].append(e.dwell_ms)

    avg_dwell_by_zone = {}
    for zone_id, zone_dwells in dwells.items():
        avg_dwell_by_zone[zone_id] = int(sum(zone_dwells) / len(zone_dwells))

    # 4. Current Queue Depth
    # Active tracks in BILLING zone around the latest timestamp
    latest_event = db.query(StoreEventDB).filter(
        StoreEventDB.store_id == store_id
    ).order_by(StoreEventDB.timestamp.desc()).first()
    
    current_queue_depth = 0
    if latest_event:
        latest_time = latest_event.timestamp
        # Count non-staff visitor IDs who entered BILLING zone in the last 1 minute and haven't exited
        active_billing = db.query(StoreEventDB.visitor_id).filter(
            StoreEventDB.store_id == store_id,
            StoreEventDB.is_staff == False,
            StoreEventDB.zone_id == "BILLING",
            StoreEventDB.timestamp >= latest_time - timedelta(minutes=1)
        ).distinct().all()
        current_queue_depth = len(active_billing)

    # 5. Abandonment Rate
    # Rate of customers who entered the billing queue but did not execute a transaction
    billing_visitors = set(e.visitor_id for e in events if e.zone_id == "BILLING")
    num_billing_visitors = len(billing_visitors)
    
    abandoned_count = 0
    if num_billing_visitors > 0:
        for v_id in billing_visitors:
            if v_id not in converted_visitors:
                # Visitor entered billing queue but had no matching transaction
                abandoned_count += 1
        abandonment_rate = abandoned_count / num_billing_visitors
    else:
        abandonment_rate = 0.0

    return {
        "store_id": store_id,
        "unique_visitors": num_unique_visitors,
        "conversion_rate": round(conversion_rate, 4),
        "avg_dwell_by_zone": avg_dwell_by_zone,
        "current_queue_depth": current_queue_depth,
        "abandonment_rate": round(abandonment_rate, 4)
    }