from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from ingestion import get_db
from models import StoreEventDB, POSTransactionDB
from metrics import get_store_metrics

router = APIRouter()

@router.get("/stores/{store_id}/anomalies")
async def get_store_anomalies(store_id: str, db: Session = Depends(get_db)):
    """
    Identifies and returns active anomalies for a store:
    - BILLING_QUEUE_SPIKE (Queue depth > 1.5x average)
    - CONVERSION_DROP (Conversion rate < 80% of average)
    - DEAD_ZONE (No visits in a zone in the last 30 minutes)
    """
    anomalies = []
    
    # 1. Fetch current metrics
    try:
        metrics = await get_store_metrics(store_id, db)
    except Exception:
        return {"store_id": store_id, "anomalies": []}
        
    current_q_depth = metrics.get("current_queue_depth", 0)
    current_conv_rate = metrics.get("conversion_rate", 0.0)
    
    # Get latest event time as current time representation
    latest_event = db.query(StoreEventDB).filter(
        StoreEventDB.store_id == store_id
    ).order_by(StoreEventDB.timestamp.desc()).first()
    
    if not latest_event:
        return {"store_id": store_id, "anomalies": []}
        
    current_time = latest_event.timestamp
    
    # 2. Check BILLING_QUEUE_SPIKE
    # Historical average queue depth (from all BILLING events in the DB)
    all_billing_events = db.query(StoreEventDB).filter(
        StoreEventDB.store_id == store_id,
        StoreEventDB.zone_id == "BILLING",
        StoreEventDB.is_staff == False,
        StoreEventDB.event_type == "BILLING_QUEUE_JOIN"
    ).all()
    
    avg_q_depth = 1.0 # fallback
    if all_billing_events:
        q_depths = [
            e.metadata_json.get("queue_depth", 1) 
            for e in all_billing_events 
            if e.metadata_json and e.metadata_json.get("queue_depth") is not None
        ]
        if q_depths:
            avg_q_depth = sum(q_depths) / len(q_depths)
            
    if current_q_depth > max(avg_q_depth * 1.5, 2):
        severity = "CRITICAL" if current_q_depth >= 4 else "WARN"
        anomalies.append({
            "anomaly_type": "BILLING_QUEUE_SPIKE",
            "severity": severity,
            "timestamp": current_time.isoformat() + "Z",
            "suggested_action": "Open billing counter 2 and dispatch support staff immediately.",
            "details": f"Current queue depth is {current_q_depth} (Historical average: {avg_q_depth:.1f})."
        })
        
    # 3. Check CONVERSION_DROP
    # Target average conversion rate is around 30% (0.30)
    target_conv_rate = 0.28
    if current_conv_rate < target_conv_rate * 0.8 and metrics.get("unique_visitors", 0) > 10:
        anomalies.append({
            "anomaly_type": "CONVERSION_DROP",
            "severity": "WARN",
            "timestamp": current_time.isoformat() + "Z",
            "suggested_action": "Check cashier station availability or run a quick customer engagement offer.",
            "details": f"Store conversion rate is {current_conv_rate*100:.1f}% (Expected threshold: {target_conv_rate*80:.1f}%)."
        })
        
    # 4. Check DEAD_ZONE
    # No visits in a product zone in the last 30 minutes
    product_zones = ["SKINCARE", "COSMETICS", "FRAGRANCE", "ACCESSORIES", "MAKEUP"]
    thirty_min_ago = current_time - timedelta(minutes=30)
    
    for zone in product_zones:
        # Check if the zone has ANY events in database (to make sure it exists/is configured)
        zone_configured = db.query(StoreEventDB).filter(
            StoreEventDB.store_id == store_id,
            StoreEventDB.zone_id == zone
        ).first()
        
        if zone_configured:
            # Check if there are any visitor enters in the last 30 minutes
            recent_visit = db.query(StoreEventDB).filter(
                StoreEventDB.store_id == store_id,
                StoreEventDB.zone_id == zone,
                StoreEventDB.event_type == "ZONE_ENTER",
                StoreEventDB.is_staff == False,
                StoreEventDB.timestamp >= thirty_min_ago
            ).first()
            
            if not recent_visit:
                anomalies.append({
                    "anomaly_type": "DEAD_ZONE",
                    "severity": "INFO",
                    "timestamp": current_time.isoformat() + "Z",
                    "suggested_action": f"Inspect display lighting and shelf stock alignment in the {zone} zone.",
                    "details": f"No visitor entries detected in the {zone} zone in the last 30 minutes."
                })
                
    return {
        "store_id": store_id,
        "anomalies": anomalies
    }

