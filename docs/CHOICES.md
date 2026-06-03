# Architectural Choices & Trade-Offs - Store Intelligence Platform

This document details the choices, options, and reasoning for the three core decisions of the Store Intelligence Platform.

---

## Decision 1: Object Detection Model

### Options Considered
1. **YOLOv8m (Medium)**: 25.9M parameters. Higher detection confidence.
2. **RT-DETR (Real-Time DEtection TRansformer)**: Transformer-based detector. State-of-the-art accuracy but high computational cost.
3. **YOLOv8n (Nano)**: 3.2M parameters. Extremely lightweight and fast.

### AI Suggestion
The AI suggested using **YOLOv8m** for retail surveillance because it handles overlapping customers (crowds) and partial occlusions with fewer false negatives.

### Choice & Rationale
I selected **YOLOv8n (Nano)**.
- **CPU Inference Limitations**: The hosting environment is CPU-only (PyTorch CPU). Running YOLOv8m on 1080p video at 15fps results in inference speeds of 300ms+ per frame, which is too slow for processing batch videos or real-time simulation. YOLOv8n achieves frame inference times of ~30-50ms on modern CPU hardware, which is near real-time.
- **ByteTrack Compensation**: Because ByteTrack maintains tracker continuity by predicting trajectories, it compensates for the slightly lower raw detection confidence of YOLOv8n, preventing track fragmentation when customers are briefly occluded.

---

## Decision 2: Event Schema Design

### Options Considered
1. **Flat Schema**: All fields (e.g. `queue_depth`, `sku_zone`, `dwell_ms`) are top-level JSON keys.
2. **Nested Metadata Object**: The event core fields are top-level keys, while all context-specific metrics are placed in a nested `metadata` dictionary.

### AI Suggestion
The AI suggested a flat schema to simplify direct SQL mapping, since flat JSON keys map 1:1 with database columns in traditional relational schemas.

### Choice & Rationale
I chose the **Nested Metadata Object** schema:
- **Database Schema Stability**: In retail settings, different camera angles produce different behavioral context (e.g., only billing counters record `queue_depth`, only shelves record `sku_zone`). A flat schema would result in dozens of columns containing mostly `NULL` values.
- **Extensibility**: Placing context-specific data inside a JSON column in SQLite (represented as a nested dictionary in Pydantic) allows us to add new fields in the future without performing database table migrations.

---

## Decision 3: API Storage (Database Selection)

### Options Considered
1. **PostgreSQL**: Robust, enterprise-ready, supports concurrent writes and JSONB indexes.
2. **SQLite**: Serverless, self-contained, stored as a local file, runs in-process.

### AI Suggestion
The AI suggested using **PostgreSQL** in a Docker container to support high concurrent write loads (event streams from 40 stores).

### Choice & Rationale
I selected **SQLite**:
- **Simplicity & Portability**: SQLite requires no external server setup, meaning `docker compose up` starts instantly without waiting for database service health checks or network handshakes.
- **Resource Constraints**: Since we are processing clips offline and replaying them, SQLite's single-file in-process writing is fast enough and uses significantly less memory than a running PostgreSQL database service.
- **Production Path**: If we scale to 40 active stores sending continuous events, we would migrate to PostgreSQL using SQLAlchemy's dialect support with a simple change of the `DATABASE_URL` environment variable.
