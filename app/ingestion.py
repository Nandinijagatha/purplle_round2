import os
import csv
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from fastapi import APIRouter, Depends, HTTPException, status, Body
from pydantic import ValidationError

from models import Base, StoreEventDB, POSTransactionDB, StoreEvent

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "store_events.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

# Ensure database directory exists
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """
    Creates tables and pre-populates POS transactions from the CSV file.
    """
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # Check if POS transactions are already pre-populated
        if db.query(POSTransactionDB).count() == 0:
            csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "Brigade_Bangalore_10_April_26 (1)bc6219c.csv")
            if os.path.exists(csv_path):
                print(f"Pre-populating database with POS transactions from {csv_path}...")
                transactions = {}
                with open(csv_path, mode="r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        order_id = row.get("order_id")
                        if not order_id:
                            continue
                        
                        # Parse order_date '10-04-2026' and order_time '20:10:30'
                        date_str = row.get("order_date")
                        time_str = row.get("order_time")
                        try:
                            dt = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M:%S")
                        except Exception:
                            # fallback to naive or current time
                            dt = datetime.now()
                            
                        store_id = row.get("store_id", "ST1008")
                        total_amount = float(row.get("total_amount", 0.0))
                        
                        if order_id not in transactions:
                            transactions[order_id] = {
                                "store_id": store_id,
                                "timestamp": dt,
                                "basket_value": 0.0
                            }
                        transactions[order_id]["basket_value"] += total_amount

                for order_id, tx_data in transactions.items():
                    tx = POSTransactionDB(
                        transaction_id=order_id,
                        store_id=tx_data["store_id"],
                        timestamp=tx_data["timestamp"],
                        basket_value=tx_data["basket_value"]
                    )
                    db.add(tx)
                db.commit()
                print("Pre-population finished successfully.")
            else:
                print(f"POS Transactions CSV not found at {csv_path}. Skipping pre-population.")
    except Exception as e:
        print(f"Error pre-populating POS transactions: {e}")
        db.rollback()
    finally:
        db.close()

# Router for Ingest Endpoint
router = APIRouter()

@router.post("/events/ingest")
async def ingest_events(payload: list = Body(...), db: Session = Depends(get_db)):
    """
    Accepts batches of up to 500 events.
    Validates, deduplicates (idempotency by event_id), and stores.
    Supports partial success for malformed records.
    """
    if len(payload) > 500:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Batch size exceeds maximum limit of 500 events"
        )
        
    accepted_count = 0
    rejected = []
    
    for idx, item in enumerate(payload):
        # Validate Pydantic schema
        try:
            event_data = StoreEvent(**item)
        except (ValidationError, TypeError) as e:
            rejected.append({
                "index": idx,
                "error": str(e),
                "payload": item
            })
            continue

        # Parse timestamp string
        try:
            # Strip trailing Z and parse
            ts_str = event_data.timestamp
            if ts_str.endswith('Z'):
                ts_str = ts_str[:-1]
            dt = datetime.fromisoformat(ts_str)
        except Exception as e:
            rejected.append({
                "index": idx,
                "error": f"Invalid timestamp format: {e}",
                "payload": item
            })
            continue

        # Check for duplicate event_id (Idempotency)
        existing = db.query(StoreEventDB).filter(StoreEventDB.event_id == event_data.event_id).first()
        if existing:
            # Already exists - ignore silently and treat as accepted (idempotency requirement)
            accepted_count += 1
            continue

        # Create DB record
        try:
            db_event = StoreEventDB(
                event_id=event_data.event_id,
                store_id=event_data.store_id,
                camera_id=event_data.camera_id,
                visitor_id=event_data.visitor_id,
                event_type=event_data.event_type,
                timestamp=dt,
                zone_id=event_data.zone_id,
                dwell_ms=event_data.dwell_ms,
                is_staff=event_data.is_staff,
                confidence=event_data.confidence,
                metadata_json=event_data.metadata.dict() if event_data.metadata else None
            )
            db.add(db_event)
            accepted_count += 1
        except Exception as e:
            db.rollback()
            rejected.append({
                "index": idx,
                "error": f"Database insertion error: {e}",
                "payload": item
            })
            continue

    # Commit the batch session
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database commit error: {e}"
        )

    # Return partial success result
    return {
        "status": "success" if len(rejected) == 0 else "partial_success",
        "accepted_count": accepted_count,
        "rejected_count": len(rejected),
        "rejected": rejected
    }