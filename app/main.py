import json
import logging
import time
import uuid
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from ingestion import router as ingest_router, init_db
from metrics import router as metrics_router
from funnel import router as funnel_router
from anomalies import router as anomalies_router
from health import router as health_router

# Setup Standard Structured Logger
logger = logging.getLogger("store_intelligence")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
formatter = logging.Formatter('%(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

app = FastAPI(
    title="Store Intelligence API",
    description="Real-time in-store analytics and anomaly detection platform",
    version="1.0.0"
)

# Initialize Database and Pre-populate POS transactions on startup
@app.on_event("startup")
def on_startup():
    init_db()

# Middleware for Structured Logging and Exception Handling
@app.middleware("http")
async def structured_logging_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-ID", str(uuid.uuid4()))
    start_time = time.time()
    
    # Try to extract store_id from path
    store_id = None
    path_parts = request.url.path.split("/")
    if "stores" in path_parts:
        try:
            store_idx = path_parts.index("stores")
            if store_idx + 1 < len(path_parts):
                store_id = path_parts[store_idx + 1]
        except ValueError:
            pass
            
    # Try to count events in ingest request payload
    event_count = 0
    if request.url.path == "/events/ingest":
        try:
            # We can't read request.json() here directly without consuming the body,
            # so we cache the body to allow the handler to read it later.
            body_bytes = await request.body()
            
            # Since request.body() caches the body in request._body, downstream handlers
            # will read it from there. Any subsequent ASGI receive calls should block indefinitely
            # to prevent Starlette from thinking the client disconnected prematurely.
            async def receive():
                import asyncio
                await asyncio.sleep(3600)
                return {"type": "http.disconnect"}
            request._receive = receive
            
            payload = json.loads(body_bytes)
            if isinstance(payload, list):
                event_count = len(payload)
                # If store_id not set, get from first event in ingest payload
                if not store_id and event_count > 0:
                    store_id = payload[0].get("store_id")
        except Exception:
            pass

    try:
        response = await call_next(request)
        latency_ms = int((time.time() - start_time) * 1000)
        
        # Log structured request info as JSON
        log_data = {
            "trace_id": trace_id,
            "store_id": store_id,
            "endpoint": request.url.path,
            "latency_ms": latency_ms,
            "event_count": event_count,
            "status_code": response.status_code
        }
        logger.info(json.dumps(log_data))
        
        # Inject Trace ID in response headers
        response.headers["X-Trace-ID"] = trace_id
        return response

    except SQLAlchemyError as db_err:
        # Graceful degradation for database connection loss
        latency_ms = int((time.time() - start_time) * 1000)
        log_data = {
            "trace_id": trace_id,
            "store_id": store_id,
            "endpoint": request.url.path,
            "latency_ms": latency_ms,
            "event_count": event_count,
            "status_code": 503,
            "error": f"Database error: {db_err}"
        }
        logger.error(json.dumps(log_data))
        
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": "Service Temporarily Unavailable",
                "message": "The database is currently unreachable. Please try again later.",
                "trace_id": trace_id
            }
        )
    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        log_data = {
            "trace_id": trace_id,
            "store_id": store_id,
            "endpoint": request.url.path,
            "latency_ms": latency_ms,
            "event_count": event_count,
            "status_code": 500,
            "error": str(e)
        }
        logger.error(json.dumps(log_data))
        
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "Internal Server Error",
                "message": "An unexpected error occurred. Please contact support.",
                "trace_id": trace_id
            }
        )

# Register Routers
app.include_router(ingest_router)
app.include_router(metrics_router)
app.include_router(funnel_router)
app.include_router(anomalies_router)
app.include_router(health_router)
