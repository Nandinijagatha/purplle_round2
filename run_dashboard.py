import sys
import os
import time
import json
import threading
import httpx
from datetime import datetime
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.console import Console

API_URL = "https://purplle-round2.onrender.com"
STORE_ID = "ST1008"
EVENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "events.jsonl")

ingestion_log = []
replayer_running = True

def simulate_realtime_ingestion():
    """
    Simulates real-time ingestion by reading events from events.jsonl
    and sending them to the API in batches with a delay.
    """
    global replayer_running, ingestion_log
    if not os.path.exists(EVENTS_FILE):
        ingestion_log.append("[bold red]Error: data/events.jsonl not found. Run pipeline/run.py first![/]")
        return
        
    ingestion_log.append("[bold green]Starting simulated live CCTV stream...[/]")
    time.sleep(2)
    
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        events = [json.loads(line) for line in f]
        
    if not events:
        ingestion_log.append("[bold red]Error: data/events.jsonl is empty![/]")
        return
        
    # Group events into small batches (e.g. 5 events at a time)
    batch_size = 5
    for i in range(0, len(events), batch_size):
        if not replayer_running:
            break
            
        batch = events[i:i+batch_size]
        try:
            r = httpx.post(f"{API_URL}/events/ingest", json=batch)
            if r.status_code == 200:
                res = r.json()
                ts = datetime.now().strftime("%H:%M:%S")
                ingestion_log.append(f"[{ts}] Ingested {res['accepted_count']} events (Type: {batch[0]['event_type']})")
            else:
                ingestion_log.append(f"[bold red]Ingestion failed with status {r.status_code}[/]")
        except Exception as e:
            ingestion_log.append(f"[bold red]Ingestion connection error: {e}[/]")
            
        time.sleep(1.5) # simulated delay between camera updates
        
    ingestion_log.append("[bold blue]CCTV stream replayed successfully.[/]")

def make_layout() -> Layout:
    layout = Layout()
    layout.split(
        Layout(name="header", size=3),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=8)
    )
    layout["body"].split_row(
        Layout(name="metrics", ratio=1),
        Layout(name="anomalies", ratio=1)
    )
    return layout

def get_header() -> Panel:
    title = Text("APEX RETAIL — STORE INTELLIGENCE SYSTEM (LIVE)", style="bold magenta", justify="center")
    return Panel(title, style="bold white")

def get_metrics_panel(metrics: dict) -> Panel:
    t = Table(expand=True, show_header=False)
    t.add_column("Metric", style="cyan bold")
    t.add_column("Value", style="green bold", justify="right")
    
    t.add_row("Store ID", metrics.get("store_id", STORE_ID))
    t.add_row("Unique Customers Today", str(metrics.get("unique_visitors", 0)))
    t.add_row("Store Conversion Rate", f"{metrics.get('conversion_rate', 0.0)*100:.2f}%")
    t.add_row("Current Cashier Queue Depth", str(metrics.get("current_queue_depth", 0)))
    t.add_row("Queue Abandonment Rate", f"{metrics.get('abandonment_rate', 0.0)*100:.2f}%")
    
    # Add Zone Dwell Table inside Panel
    t_dwell = Table(title="Average Customer Dwell Times", expand=True)
    t_dwell.add_column("Store Zone", style="yellow")
    t_dwell.add_column("Avg Dwell (Seconds)", style="magenta", justify="right")
    
    for zone, ms in metrics.get("avg_dwell_by_zone", {}).items():
        t_dwell.add_row(zone, f"{ms/1000:.1f}s")
        
    main_layout = Layout()
    main_layout.split(
        Layout(Panel(t, title="Core Metrics")),
        Layout(Panel(t_dwell, title="Zone Dwells"))
    )
    return Panel(t, title="Real-Time Analytics Metrics")

def get_anomalies_panel(anomalies_data: list) -> Panel:
    t = Table(expand=True)
    t.add_column("Time", style="dim")
    t.add_column("Anomaly Type", style="bold red")
    t.add_column("Severity", style="bold")
    t.add_column("Action", style="yellow")
    
    for a in anomalies_data:
        sev = a["severity"]
        if sev == "CRITICAL":
            sev_str = f"[bold red]{sev}[/]"
        elif sev == "WARN":
            sev_str = f"[bold yellow]{sev}[/]"
        else:
            sev_str = f"[bold blue]{sev}[/]"
            
        t.add_row(
            a["timestamp"].split("T")[-1][:8],
            a["anomaly_type"],
            sev_str,
            a["suggested_action"]
        )
        
    return Panel(t, title="Active Operations Anomalies")

def get_footer_panel() -> Panel:
    # Print the last 6 lines of ingestion logs
    recent_logs = ingestion_log[-5:]
    log_text = Text()
    for log in recent_logs:
        log_text.append(Text.from_markup(log + "\n"))
    return Panel(log_text, title="Event Ingestion Feed Log")

def main():
    global replayer_running
    
    # Start ingestion simulation thread
    t = threading.Thread(target=simulate_realtime_ingestion, daemon=True)
    t.start()
    
    console = Console()
    layout = make_layout()
    
    # Loop UI update
    with Live(layout, refresh_per_second=1, screen=True) as live:
        try:
            while True:
                # 1. Fetch metrics from local API
                metrics = {}
                try:
                    r = httpx.get(f"{API_URL}/stores/{STORE_ID}/metrics")
                    if r.status_code == 200:
                        metrics = r.json()
                except Exception:
                    pass
                    
                # 2. Fetch anomalies
                anomalies = []
                try:
                    r = httpx.get(f"{API_URL}/stores/{STORE_ID}/anomalies")
                    if r.status_code == 200:
                        anomalies = r.json().get("anomalies", [])
                except Exception:
                    pass
                
                # 3. Update layout components
                layout["header"].update(get_header())
                
                # Create Table of Metrics
                t_metrics = Table(expand=True, show_header=True)
                t_metrics.add_column("Metric Description", style="cyan bold")
                t_metrics.add_column("Live Value", style="bold green", justify="right")
                t_metrics.add_row("Store Name", "Brigade Road, Bangalore (ST1008)")
                t_metrics.add_row("Unique Visitors", str(metrics.get("unique_visitors", 0)))
                t_metrics.add_row("Conversion Rate", f"{metrics.get('conversion_rate', 0.0)*100:.2f}%")
                t_metrics.add_row("Billing Queue Depth", str(metrics.get("current_queue_depth", 0)))
                t_metrics.add_row("Queue Abandonment Rate", f"{metrics.get('abandonment_rate', 0.0)*100:.2f}%")
                
                t_dwell = Table(expand=True, show_header=True)
                t_dwell.add_column("Product Zone", style="bold yellow")
                t_dwell.add_column("Avg Dwell", style="bold magenta", justify="right")
                
                avg_dwells = metrics.get("avg_dwell_by_zone", {})
                if avg_dwells:
                    for zone, ms in avg_dwells.items():
                        t_dwell.add_row(zone, f"{ms/1000:.1f} seconds")
                else:
                    t_dwell.add_row("No dwell data yet", "-")
                
                # Split metrics layout
                metrics_grid = Table.grid(expand=True)
                metrics_grid.add_row(Panel(t_metrics, title="Key Store Statistics"))
                metrics_grid.add_row(Panel(t_dwell, title="Zone Dwell Time Analytics"))
                
                layout["body"]["metrics"].update(metrics_grid)
                layout["body"]["anomalies"].update(get_anomalies_panel(anomalies))
                layout["footer"].update(get_footer_panel())
                
                time.sleep(1)
        except KeyboardInterrupt:
            replayer_running = False
            
if __name__ == "__main__":
    main()
