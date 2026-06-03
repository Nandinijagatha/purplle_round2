# Store Intelligence Platform

An end-to-end computer vision and real-time analytics platform built for **Apex Retail** to monitor store traffic, cashier queues, conversion rates, and anomalies.

---

## Quick Start (Running Locally)

To set up the environment and run the entire pipeline end-to-end, execute these commands:

### 1. Install Dependencies
```bash
python -m pip install -r requirements.txt
```

### 2. Process CCTV Clips (Run CV Pipeline)
Process the raw CCTV video files through YOLOv8 and ByteTrack to generate the behavioral event stream:
```bash
python pipeline/run.py
```
This will read the camera clips and generate a unified event stream in `data/events.jsonl`.

### 3. Launch the Backend API
Start the FastAPI server:
```bash
uvicorn app.main:app --reload
```
On startup, the API automatically pre-populates its SQLite database with the POS transactions from the CSV file (`data/Brigade_Bangalore_10_April_26 (1)bc6219c.csv`).

### 4. Start the Live Terminal Dashboard
In a separate terminal, launch the dashboard:
```bash
python run_dashboard.py
```
This dashboard will launch a simulated stream by reading events from `data/events.jsonl`, posting them to the ingestion endpoint (`POST /events/ingest`), and displaying updating analytics, conversion funnels, and operational anomalies.

---

## Running with Docker Compose

To start both the API and the live terminal dashboard in containerized environments:

```bash
docker compose up --build
```
- The FastAPI backend will be available at `http://localhost:8000`.
- The live dashboard will render directly in the terminal output.

---

## API Endpoints

- **`POST /events/ingest`**: Receives batches of up to 500 visitor events. Safe to call multiple times (idempotent by `event_id`).
- **`GET /stores/{id}/metrics`**: Real-time stats (visitor count, conversion rate, cashier queue depth, average zone dwells, queue abandonment rate).
- **`GET /stores/{id}/funnel`**: Four-stage conversion funnel: Entry -> Zone Visit -> Billing Queue -> Purchase.
- **`GET /stores/{id}/anomalies`**: Operational alerts: `BILLING_QUEUE_SPIKE`, `CONVERSION_DROP`, `DEAD_ZONE`.
- **`GET /health`**: API health check and event feed freshness monitoring (detects `STALE_FEED` if lag > 10 mins).

---

## Running Automated Tests

Run the unit test suite to verify pipeline analytics and endpoints:
```bash
python -m pytest
```
All tests should pass.
