# System Design & Architecture - Store Intelligence Platform

This document describes the design, data pipelines, and architectural decisions behind the Store Intelligence Platform implemented for Apex Retail.

---

## System Architecture Overview

The system is designed as an end-to-end, decoupled retail analytics pipeline divided into three layers:

1. **Object Detection & Tracking (CV Pipeline)**:
   - Evaluates raw 1080p, 15fps video streams using a pre-trained **YOLOv8n** model running on CPU.
   - Leverages **ByteTrack** via the `supervision` library to maintain identity tracking within a camera feed.
   - Features a **ReIDTracker** that tracks exited visitor metadata in a 5-minute sliding window to deduplicate re-entries, converting double entry counts into a single continuous session.
   - Features a **Staff Uniform Detector** combining HSV black-color thresholding on the upper-body bounding box crop, spatial boundaries (backroom, cash counter), and activity duration to flag and filter staff.
   - Uses normalized 2D polygons mapped to the store layout blueprint to check visitor zone occupancy (`SKINCARE`, `COSMETICS`, `FRAGRANCE`, `MAKEUP`, `BILLING`, `PMU`, `BACKROOM`).

2. **Backend Storage & API Services**:
   - Built with **FastAPI** for high-throughput async request handling.
   - Uses **SQLite** for relational event and POS transaction storage, structured with indexes on `store_id`, `visitor_id`, and `timestamp` to ensure instant metric queries.
   - Features batch ingestion (up to 500 events) with strict idempotency (using SQLite primary key constraints on `event_id`) and partial success error reporting.
   - Implements structured JSON request/response logging, and standard error handling to gracefully degrade to HTTP 503 if the database is locked or unavailable.

3. **Live UI Dashboard**:
   - A command-line terminal visualization built with `rich` that displays store metrics (visitors, conversions, queues, zone dwells, active anomalies) updating live by polling the FastAPI endpoints.

---

## AI-Assisted Decisions

During development, LLM tools were utilized to evaluate design options, build test cases, and analyze cv model performance. The following details where AI recommendations shaped or were overridden in the final architecture:

### 1. Object Detection Model Selection (Agreed)
- **AI Recommendation**: The LLM compared YOLOv8m (medium) vs. YOLOv8n (nano) vs. RT-DETR. It suggested YOLOv8m as the ideal balance between detection confidence and speed for retail footage.
- **Decision & Override**: Upon verifying that the runner system lacks GPU acceleration (CPU-only PyTorch environment), I overrode the recommendation to use YOLOv8m and chose **YOLOv8n**. YOLOv8n has a much smaller parameter footprint (3.2M parameters compared to YOLOv8m's 25.9M) and is able to run near real-time on standard CPU hosts, which prevented video pipeline processing timeouts.

### 2. Event Schema Design (Overridden)
- **AI Recommendation**: The LLM proposed a flat JSON schema where extra parameters like `queue_depth` or `sku_zone` were top-level properties (e.g. `{"event_id": ..., "queue_depth": 3, "sku_zone": "skincare"}`).
- **Decision & Override**: I overrode this design to use a nested `metadata` dictionary. Grouping optional fields (like `sku_zone`, `queue_depth`, `session_seq`) into a single `metadata` JSON object keeps the primary database table schema clean, prevents wide sparse tables, and allows easy extensibility for future camera sensors without requiring database migrations.

### 3. Re-ID Approach (Agreed)
- **AI Recommendation**: The LLM suggested using an appearance-based Re-ID model (e.g., extracting ResNet/OSNet deep embeddings for each person's face/body crop and running cosine similarity).
- **Decision & Override**: Because the raw CCTV clips have full-face blur applied for customer anonymity and staff wear identical black uniforms, appearance-based features are highly noisy. I instead implemented a **trajectory and time-window-based Re-ID approach**: tracking visitor size/entry-coordinates and utilizing a 5-minute cooldown window for exits. This ensures robust re-entry matching without expensive, face-dependent visual Re-ID.
