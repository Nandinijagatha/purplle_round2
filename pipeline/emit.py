import uuid
import json
from datetime import datetime

def emit_event(store_id, camera_id, visitor_id, event_type,
               timestamp, zone_id=None, dwell_ms=0,
               is_staff=False, confidence=1.0, metadata=None):
    """
    Constructs an event in the required JSON schema format.
    Args:
        timestamp: datetime object or ISO string.
    """
    if isinstance(timestamp, datetime):
        # Enforce Z suffix for UTC
        ts_str = timestamp.isoformat()
        if not ts_str.endswith('Z') and '+' not in ts_str:
            ts_str += 'Z'
    else:
        ts_str = str(timestamp)

    default_metadata = {
        "queue_depth": None,
        "sku_zone": None,
        "session_seq": 1
    }
    if metadata:
        default_metadata.update(metadata)

    event = {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": ts_str,
        "zone_id": zone_id,
        "dwell_ms": int(dwell_ms),
        "is_staff": bool(is_staff),
        "confidence": round(float(confidence), 4),
        "metadata": default_metadata
    }
    return event