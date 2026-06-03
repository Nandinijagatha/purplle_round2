from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, JSON
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

# SQLAlchemy Database Models
class StoreEventDB(Base):
    __tablename__ = "events"
    
    event_id = Column(String(36), primary_key=True, index=True)
    store_id = Column(String(50), nullable=False, index=True)
    camera_id = Column(String(50), nullable=False)
    visitor_id = Column(String(50), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    zone_id = Column(String(50), nullable=True)
    dwell_ms = Column(Integer, default=0)
    is_staff = Column(Boolean, default=False)
    confidence = Column(Float, default=1.0)
    metadata_json = Column(JSON, nullable=True)

class POSTransactionDB(Base):
    __tablename__ = "pos_transactions"
    
    transaction_id = Column(String(50), primary_key=True, index=True)
    store_id = Column(String(50), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    basket_value = Column(Float, nullable=False)

# Pydantic Schemas for Ingestion Request and Responses
class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = 0

class StoreEvent(BaseModel):
    event_id: str = Field(..., description="UUID-v4 globally unique ID")
    store_id: str = Field(..., description="Retail store identifier")
    camera_id: str = Field(..., description="CCTV Camera identifier")
    visitor_id: str = Field(..., description="Re-ID visitor token unique per visit")
    event_type: str = Field(..., description="Type of behavioral event")
    timestamp: str = Field(..., description="ISO-8601 UTC timestamp")
    zone_id: Optional[str] = None
    dwell_ms: Optional[int] = 0
    is_staff: Optional[bool] = False
    confidence: Optional[float] = 1.0
    metadata: Optional[EventMetadata] = None