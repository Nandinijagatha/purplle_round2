from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timezone
from typing import Dict, Any

from ingestion import get_db
from models import StoreEventDB

router = APIRouter()

@router.get("/health")
async def get_health(db: Session = Depends(get_db)):
    """
    Checks service status, database connectivity, and monitors event feed freshness.
    Triggers STALE_FEED warning if the last event lag is > 10 minutes.
    """
    # 1. Check Database Connectivity
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database connection failed: {e}"
        )

    # 2. Get Last Event Timestamp per store
    stores_health = {}
    try:
        # Get unique store IDs in database
        store_ids = db.query(StoreEventDB.store_id).distinct().all()
        store_ids = [s[0] for s in store_ids]
        
        for s_id in store_ids:
            latest_event = db.query(StoreEventDB).filter(
                StoreEventDB.store_id == s_id
            ).order_by(StoreEventDB.timestamp.desc()).first()
            
            if latest_event:
                last_event_time = latest_event.timestamp
                # Calculate lag relative to actual current time
                current_time = datetime.now()
                lag_seconds = (current_time - last_event_time).total_seconds()
                
                # Check for stale feed (> 10 minutes = 600 seconds)
                feed_status = "STALE_FEED" if lag_seconds > 600 else "LIVE"
                
                stores_health[s_id] = {
                    "last_event_timestamp": last_event_time.isoformat() + "Z",
                    "lag_minutes": round(lag_seconds / 60, 2),
                    "feed_status": feed_status
                }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error checking event lag: {e}"
        )

    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat() + "Z",
        "database": "connected",
        "stores": stores_health
    }