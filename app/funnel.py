from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import timedelta
from ingestion import get_db
from models import StoreEventDB, POSTransactionDB

router = APIRouter()

@router.get("/stores/{store_id}/funnel")
async def get_store_funnel(store_id: str, db: Session = Depends(get_db)):
    """
    Returns conversion funnel analysis for a store:
    Entry -> Zone Visit -> Billing Queue -> Purchase
    Excludes is_staff=True events.
    """
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
            "funnel": {
                "Entry": {"count": 0, "drop_off_pct": 0.0},
                "Zone_Visit": {"count": 0, "drop_off_pct": 0.0},
                "Billing_Queue": {"count": 0, "drop_off_pct": 0.0},
                "Purchase": {"count": 0, "drop_off_pct": 0.0}
            }
        }

    # 1. Total Unique Visitors (Entry Stage)
    entry_visitors = set(e.visitor_id for e in events)
    count_entry = len(entry_visitors)

    # 2. Zone Visit Stage
    # Any visitor who entered a product zone (skincare, cosmetics, fragrance, accessories, makeup)
    product_zones = {"SKINCARE", "COSMETICS", "FRAGRANCE", "ACCESSORIES", "MAKEUP"}
    zone_visitors = set(
        e.visitor_id for e in events 
        if e.zone_id in product_zones
    )
    count_zone = len(zone_visitors)

    # 3. Billing Queue Stage
    # Any visitor who entered the billing zone
    billing_visitors = set(
        e.visitor_id for e in events 
        if e.zone_id == "BILLING"
    )
    count_billing = len(billing_visitors)

    # 4. Purchase Stage
    # Any visitor who is correlated with a transaction
    purchase_visitors = set()
    for txn in transactions:
        txn_time = txn.timestamp
        window_start = txn_time - timedelta(minutes=5)
        billing_events = [
            e.visitor_id for e in events
            if e.zone_id == "BILLING" and window_start <= e.timestamp <= txn_time
        ]
        for v_id in billing_events:
            purchase_visitors.add(v_id)
            
    count_purchase = len(purchase_visitors)

    # Calculate drop-off percentages
    drop_off_zone = 0.0
    if count_entry > 0:
        drop_off_zone = round(((count_entry - count_zone) / count_entry) * 100, 2)

    drop_off_billing = 0.0
    if count_zone > 0:
        drop_off_billing = round(((count_zone - count_billing) / count_zone) * 100, 2)

    drop_off_purchase = 0.0
    if count_billing > 0:
        drop_off_purchase = round(((count_billing - count_purchase) / count_billing) * 100, 2)

    return {
        "store_id": store_id,
        "funnel": {
            "Entry": {
                "count": count_entry,
                "drop_off_pct": 0.0
            },
            "Zone_Visit": {
                "count": count_zone,
                "drop_off_pct": drop_off_zone
            },
            "Billing_Queue": {
                "count": count_billing,
                "drop_off_pct": drop_off_billing
            },
            "Purchase": {
                "count": count_purchase,
                "drop_off_pct": drop_off_purchase
            }
        }
    }
