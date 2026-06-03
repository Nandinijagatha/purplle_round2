import sys
import os
import json
import time
import asyncio
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta

# Ensure python path can find app modules
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'app')))

from app.ingestion import SessionLocal, init_db, DB_PATH, BASE_DIR
from app.models import StoreEventDB, POSTransactionDB
from app.metrics import get_store_metrics
from app.funnel import get_store_funnel
from app.anomalies import get_store_anomalies

# Page Configuration
st.set_page_config(
    page_title="Apex Retail — Store Intelligence",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Dark Theme Custom Styling
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

/* Glassmorphic Metrics Card */
.metric-card {
    background: rgba(30, 41, 59, 0.45);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    padding: 1.5rem;
    box-shadow: 0 4px 30px rgba(0, 0, 0, 0.2);
    transition: transform 0.2s ease, border-color 0.2s ease;
    text-align: center;
    margin-bottom: 1rem;
}
.metric-card:hover {
    transform: translateY(-2px);
    border-color: rgba(168, 85, 247, 0.4);
}
.metric-value {
    font-size: 2.25rem;
    font-weight: 800;
    margin-top: 0.5rem;
    margin-bottom: 0.25rem;
    background: linear-gradient(135deg, #a855f7 0%, #3b82f6 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}
.metric-label {
    font-size: 0.85rem;
    font-weight: 600;
    color: #94a3b8;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

/* Operational Alerts */
.anomaly-item {
    padding: 1rem;
    border-radius: 8px;
    margin-bottom: 0.75rem;
    border-left: 5px solid;
    color: #f8fafc;
}
.anomaly-critical {
    background: rgba(239, 68, 68, 0.12);
    border-left-color: #ef4444;
    border: 1px solid rgba(239, 68, 68, 0.25);
    border-left-width: 5px;
}
.anomaly-warn {
    background: rgba(245, 158, 11, 0.12);
    border-left-color: #f59e0b;
    border: 1px solid rgba(245, 158, 11, 0.25);
    border-left-width: 5px;
}
.anomaly-info {
    background: rgba(59, 130, 246, 0.12);
    border-left-color: #3b82f6;
    border: 1px solid rgba(59, 130, 246, 0.25);
    border-left-width: 5px;
}
.anomaly-header {
    font-weight: 700;
    font-size: 1rem;
    margin-bottom: 0.25rem;
}
.anomaly-details {
    font-size: 0.875rem;
    color: #cbd5e1;
    margin-bottom: 0.5rem;
}
.anomaly-action {
    font-size: 0.875rem;
    font-weight: 600;
    color: #38bdf8;
}

/* Ingestion feed log panel */
.feed-log-panel {
    background: #0f172a;
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 8px;
    padding: 1rem;
    font-family: monospace;
    font-size: 0.8rem;
    height: 200px;
    overflow-y: auto;
    color: #38bdf8;
}
</style>
""", unsafe_allow_html=True)

# Application Parameters
STORE_ID = "ST1008"
EVENTS_FILE = os.path.join(BASE_DIR, "data", "events.jsonl")

# Helper to run async tasks in Streamlit
def run_async(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)

# Initialize Session State
if "event_cursor" not in st.session_state:
    st.session_state.event_cursor = 0
if "simulation_active" not in st.session_state:
    st.session_state.simulation_active = False
if "ingestion_log" not in st.session_state:
    st.session_state.ingestion_log = ["[System] Ready to ingest live streams."]

# Ingestion Database Helper (Idempotent & Local)
def db_ingest_batch(batch, db):
    added_count = 0
    for item in batch:
        try:
            event_id = item.get("event_id")
            if not event_id:
                continue
                
            # Idempotency Check
            existing = db.query(StoreEventDB).filter(StoreEventDB.event_id == event_id).first()
            if existing:
                continue
                
            ts_str = item.get("timestamp")
            if ts_str.endswith('Z'):
                ts_str = ts_str[:-1]
            dt = datetime.fromisoformat(ts_str)
            
            db_event = StoreEventDB(
                event_id=event_id,
                store_id=item.get("store_id"),
                camera_id=item.get("camera_id"),
                visitor_id=item.get("visitor_id"),
                event_type=item.get("event_type"),
                timestamp=dt,
                zone_id=item.get("zone_id"),
                dwell_ms=item.get("dwell_ms", 0),
                is_staff=item.get("is_staff", False),
                confidence=item.get("confidence", 1.0),
                metadata_json=item.get("metadata")
            )
            db.add(db_event)
            added_count += 1
        except Exception as e:
            st.sidebar.error(f"Ingestion error: {e}")
    db.commit()
    return added_count

# Reset Database Helper
def reset_database(db):
    # Truncate events table
    db.query(StoreEventDB).delete()
    db.commit()
    # Reset transactions (re-populate)
    init_db()
    st.session_state.event_cursor = 0
    st.session_state.ingestion_log = ["[System] Database reset and POS pre-populated successfully."]

# Load All CCTV Events
all_cctv_events = []
if os.path.exists(EVENTS_FILE):
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        all_cctv_events = [json.loads(line) for line in f]

# Render Title Header
st.markdown("<h1 style='text-align: center; margin-bottom: 0px;'>🏬 APEX RETAIL</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align: center; color: #94a3b8; font-size: 1.1rem; margin-top: 5px; margin-bottom: 30px;'>Real-Time Computer Vision Store Intelligence Platform</p>", unsafe_allow_html=True)

# ----------------- SIDEBAR SIMULATION CONTROL -----------------
st.sidebar.header("🕹️ Stream Controller")
st.sidebar.markdown("Use these controls to simulate the CCTV edge stream pipeline feeding events into the analytics platform.")

db = SessionLocal()

# Database Status Indicator
if os.path.exists(DB_PATH):
    st.sidebar.success("Database Status: Connected")
else:
    st.sidebar.warning("Database Status: Initializing...")
    init_db()

# Event Count Display
current_events_in_db = db.query(StoreEventDB).count()
st.sidebar.metric("Events in Database", current_events_in_db)

# Control Buttons
col_side1, col_side2 = st.sidebar.columns(2)
with col_side1:
    if st.button("Reset DB"):
        reset_database(db)
        st.rerun()

with col_side2:
    if st.button("Ingest 25"):
        if all_cctv_events:
            cursor = st.session_state.event_cursor
            batch = all_cctv_events[cursor:cursor+25]
            if batch:
                added = db_ingest_batch(batch, db)
                st.session_state.event_cursor += len(batch)
                ts = datetime.now().strftime("%H:%M:%S")
                st.session_state.ingestion_log.append(f"[{ts}] Ingested {added} events (Batch count: {len(batch)})")
                st.rerun()
            else:
                st.sidebar.info("All events already ingested!")
        else:
            st.sidebar.error("Events file not found. Run CV pipeline first.")

# Real-Time Playback Toggle
st.sidebar.subheader("Live CCTV Simulation")
speed = st.sidebar.slider("Stream Speed (sec/batch)", min_value=0.5, max_value=5.0, value=1.5, step=0.5)

if st.session_state.simulation_active:
    if st.sidebar.button("Stop Live Feed ⏸️", type="primary"):
        st.session_state.simulation_active = False
        st.rerun()
else:
    if st.sidebar.button("Start Live Feed ▶️"):
        if all_cctv_events:
            st.session_state.simulation_active = True
            st.rerun()
        else:
            st.sidebar.error("No events loaded to replay.")

# Simulation playback loop
if st.session_state.simulation_active:
    cursor = st.session_state.event_cursor
    batch = all_cctv_events[cursor:cursor+5]
    if batch:
        added = db_ingest_batch(batch, db)
        st.session_state.event_cursor += len(batch)
        ts = datetime.now().strftime("%H:%M:%S")
        st.session_state.ingestion_log.append(f"[{ts}] Ingested {added} events (CCTV Live Replay)")
        time.sleep(speed)
        st.rerun()
    else:
        st.session_state.simulation_active = False
        st.sidebar.info("CCTV Stream Replay Completed!")
        st.rerun()

# ----------------- MAIN LAYOUT -----------------

# Fetch Metrics & Anomalies
metrics = run_async(get_store_metrics(STORE_ID, db))
funnel_data = run_async(get_store_funnel(STORE_ID, db))
anomalies_data = run_async(get_store_anomalies(STORE_ID, db))

# Metric Card Columns
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">👥 Unique Customers</div>
        <div class="metric-value">{metrics.get("unique_visitors", 0)}</div>
    </div>
    """, unsafe_allow_html=True)

with col2:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">📈 Conversion Rate</div>
        <div class="metric-value">{metrics.get("conversion_rate", 0.0)*100:.1f}%</div>
    </div>
    """, unsafe_allow_html=True)

with col3:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">🧍 Cashier Queue Depth</div>
        <div class="metric-value">{metrics.get("current_queue_depth", 0)}</div>
    </div>
    """, unsafe_allow_html=True)

with col4:
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-label">🚪 Queue Abandonment</div>
        <div class="metric-value">{metrics.get("abandonment_rate", 0.0)*100:.1f}%</div>
    </div>
    """, unsafe_allow_html=True)

# Tabs for visual graphs
st.markdown("### 📊 Store Analytics Visualizations")
tab1, tab2, tab3 = st.tabs(["🛒 Customer Conversion Funnel", "⏱️ Zone Dwell Times", "📈 Hourly Traffic Trends"])

with tab1:
    stages = list(funnel_data["funnel"].keys())
    counts = [funnel_data["funnel"][s]["count"] for s in stages]
    stage_labels = [s.replace("_", " ") for s in stages]
    
    fig_funnel = go.Figure(go.Funnel(
        y=stage_labels,
        x=counts,
        textposition="inside",
        textinfo="value+percent initial",
        opacity=0.85,
        marker={"color": ["#a855f7", "#8b5cf6", "#6366f1", "#3b82f6"],
                "line": {"width": [3, 2, 1, 0], "color": ["#ffffff"]*4}}
    ))
    fig_funnel.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": "#e2e8f0", "family": "Inter"},
        margin={"t": 30, "b": 10, "l": 20, "r": 20},
        height=320
    )
    st.plotly_chart(fig_funnel, use_container_width=True)

with tab2:
    avg_dwells = metrics.get("avg_dwell_by_zone", {})
    if avg_dwells:
        zones = list(avg_dwells.keys())
        seconds = [ms / 1000 for ms in avg_dwells.values()]
        
        df_dwell = pd.DataFrame({"Zone": zones, "Dwell": seconds})
        fig_dwell = px.bar(
            df_dwell,
            x="Dwell",
            y="Zone",
            orientation='h',
            labels={'Dwell': 'Average Dwell (Seconds)', 'Zone': 'Store Zone'},
            color="Dwell",
            color_continuous_scale=px.colors.sequential.Purples
        )
        fig_dwell.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font={"color": "#e2e8f0", "family": "Inter"},
            coloraxis_showscale=False,
            margin={"t": 30, "b": 10, "l": 20, "r": 20},
            height=320
        )
        st.plotly_chart(fig_dwell, use_container_width=True)
    else:
        st.info("No customer dwell time statistics calculated yet. Start simulation stream to generate zone events.")

with tab3:
    entries = db.query(StoreEventDB.timestamp).filter(
        StoreEventDB.store_id == STORE_ID,
        StoreEventDB.event_type == "ENTRY",
        StoreEventDB.is_staff == False
    ).order_by(StoreEventDB.timestamp).all()
    
    if entries:
        df_entries = pd.DataFrame([e[0] for e in entries], columns=["timestamp"])
        df_entries["Hour"] = df_entries["timestamp"].dt.strftime("%H:00")
        hourly_counts = df_entries.groupby("Hour").size().reset_index(name="Visitors")
        
        fig_trend = px.area(
            hourly_counts,
            x="Hour",
            y="Visitors",
            color_discrete_sequence=["#3b82f6"]
        )
        fig_trend.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font={"color": "#e2e8f0", "family": "Inter"},
            xaxis={"gridcolor": "rgba(255,255,255,0.04)"},
            yaxis={"gridcolor": "rgba(255,255,255,0.04)"},
            margin={"t": 30, "b": 10, "l": 20, "r": 20},
            height=320
        )
        st.plotly_chart(fig_trend, use_container_width=True)
    else:
        st.info("Waiting for visitor traffic entries. Start simulation stream to view customer traffic charts.")

# Anomalies & Ingestion Feeds
st.markdown("---")
col_bottom1, col_bottom2 = st.columns(2)

with col_bottom1:
    st.markdown("### 🚨 Active Operations Anomalies")
    anomalies = anomalies_data.get("anomalies", [])
    if anomalies:
        for a in anomalies:
            severity = a.get("severity", "INFO")
            class_map = {
                "CRITICAL": "anomaly-critical",
                "WARN": "anomaly-warn",
                "INFO": "anomaly-info"
            }
            css_class = class_map.get(severity, "anomaly-info")
            st.markdown(f"""
            <div class="anomaly-item {css_class}">
                <div class="anomaly-header">[{severity}] {a.get("anomaly_type")}</div>
                <div class="anomaly-details">{a.get("details")}</div>
                <div class="anomaly-action">🎯 Suggested Action: {a.get("suggested_action")}</div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.success("All store operations are functioning normally. No active anomalies detected.")

with col_bottom2:
    st.markdown("### 📥 Event Ingestion Live Feed Logs")
    log_content = ""
    for log in reversed(st.session_state.ingestion_log[-8:]):
        log_content += log + "\n"
    
    formatted_log_content = log_content.replace("\n", "<br>")

    st.markdown(f"""
        <div class="feed-log-panel">
            {formatted_log_content}
        </div>
    """, unsafe_allow_html=True)

# Close DB Connection
db.close()