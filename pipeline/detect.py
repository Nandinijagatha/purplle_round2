import cv2
import json
import os
import uuid
import argparse
from datetime import datetime, timedelta
from ultralytics import YOLO
import supervision as sv
import numpy as np

from tracker import ReIDTracker
from emit import emit_event

# Store and Camera IDs mapping
STORE_ID = "ST1008"

# Video start timestamps (UTC) on April 10, 2026
VIDEO_START_TIMES = {
    "CAM_1": datetime(2026, 4, 10, 20, 10, 27), # Main Skincare
    "CAM_2": datetime(2026, 4, 10, 20, 10,  3), # Main Cosmetics
    "CAM_3": datetime(2026, 4, 10, 20,  9, 47), # Entry / Exit
    "CAM_4": datetime(2026, 4, 10, 20,  9, 45), # Backroom
    "CAM_5": datetime(2026, 4, 10, 20,  9, 47)  # Billing / Counter
}

# 2D Polygons for Zone Classification (normalized coordinates x, y)
ZONE_POLYGONS = {
    "CAM_1": {
        "SKINCARE": np.array([[0.0, 0.0], [0.8, 0.0], [0.8, 0.65], [0.0, 0.65]]),
        "MAKEUP": np.array([[0.8, 0.0], [1.0, 0.0], [1.0, 0.85], [0.8, 0.85]]),
        "FRAGRANCE": np.array([[0.15, 0.35], [0.65, 0.35], [0.65, 0.95], [0.15, 0.95]])
    },
    "CAM_2": {
        "COSMETICS": np.array([[0.3, 0.0], [1.0, 0.0], [1.0, 0.9], [0.3, 0.9]]),
        "MAKEUP": np.array([[0.0, 0.0], [0.3, 0.0], [0.3, 0.9], [0.0, 0.9]]),
        "ACCESSORIES": np.array([[0.05, 0.0], [0.25, 0.0], [0.25, 0.35], [0.05, 0.35]])
    },
    "CAM_5": {
        "BILLING": np.array([[0.0, 0.0], [0.65, 0.0], [0.65, 1.0], [0.0, 1.0]]),
        "PMU": np.array([[0.65, 0.35], [1.0, 0.35], [1.0, 1.0], [0.65, 1.0]])
    }
}

def is_point_in_polygon(point, polygon):
    """
    Checks if a normalized point (x, y) is inside a polygon.
    """
    x, y = point
    n = len(polygon)
    inside = False
    p1x, p1y = polygon[0]
    for i in range(n + 1):
        p2x, p2y = polygon[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xints = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xints:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside

def process_video(video_path, output_path, append, tracker):
    video_name = os.path.splitext(os.path.basename(video_path))[0]
    # Map video name to Camera ID (e.g. "CAM 1" -> "CAM_1")
    camera_id = video_name.replace(" ", "_")
    
    start_time = VIDEO_START_TIMES.get(camera_id, datetime(2026, 4, 10, 20, 10, 0))
    
    # Initialize YOLOv8 and ByteTrack
    model = YOLO("yolov8n.pt")
    byte_tracker = sv.ByteTrack()
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error opening video file: {video_path}")
        return
        
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 25.0
        
    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    # Track visitor states: visitor_id -> {zone_id: enter_time}
    visitor_zones = {}
    # Track visitor dwell accumulators: visitor_id -> {zone_id: {last_dwell_emit_time, start_time}}
    visitor_dwells = {}
    # Track visitor position history for ENTRY/EXIT threshold crossing (CAM 3)
    # visitor_id -> list of x-coordinates (normalized)
    visitor_positions = {}
    
    # Queue depth tracking (for CAM 5)
    billing_customers = set()

    mode = "w" if not append else "a"
    out_file = open(output_path, mode, encoding="utf-8")
    
    print(f"Processing {camera_id}... File: {video_path}")
    frame_idx = 0
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
            
        current_time = start_time + timedelta(seconds=frame_idx / fps)
        
        # Run detection
        results = model(frame, verbose=False)[0]
        # Filter detections: class 0 is person
        detections = sv.Detections.from_ultralytics(results)
        detections = detections[detections.class_id == 0]
        
        # Pass to ByteTrack tracker
        tracks = byte_tracker.update_with_detections(detections)
        
        # Active tracks inside the billing zone in this frame
        current_billing_customers = set()
        
        for track in tracks:
            # track syntax in supervision: track[0] is bbox (x1, y1, x2, y2), track[4] is track_id
            bbox = track[0]
            confidence = track[2]
            track_id = int(track[4])
            
            x1, y1, x2, y2 = bbox
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2
            norm_cx = cx / frame_width
            norm_cy = cy / frame_height
            norm_center = (norm_cx, norm_cy)
            
            # 1. Re-ID and base tracking
            visitor_id, event_nature, is_staff_member = tracker.get_visitor_id(track_id, bbox, current_time, camera_id)
            
            # Check if this person is staff (dynamically classify them)
            if not is_staff_member:
                is_staff_member = tracker.classify_staff(frame, bbox, camera_id, norm_center)
                if is_staff_member:
                    tracker.mark_as_staff(visitor_id)
                    
            # 2. Camera-Specific Logics
            if camera_id == "CAM_3": # Entry camera
                # Threshold logic: outside is x > 0.55 (dark stone floor), inside is x <= 0.55 (wood floor)
                is_inside = norm_cx <= 0.55
                
                if visitor_id not in visitor_positions:
                    visitor_positions[visitor_id] = []
                pos_history = visitor_positions[visitor_id]
                pos_history.append(is_inside)
                
                # Check for crossing (need at least 5 frames of history)
                if len(pos_history) >= 5:
                    prev_inside = pos_history[-5]
                    curr_inside = pos_history[-1]
                    
                    if not prev_inside and curr_inside:
                        # Crossed inward: ENTRY/REENTRY
                        e_type = "REENTRY" if event_nature == "REENTRY" else "ENTRY"
                        evt = emit_event(STORE_ID, camera_id, visitor_id, e_type, current_time, is_staff=is_staff_member, confidence=confidence)
                        out_file.write(json.dumps(evt) + "\n")
                        # Clear history of this cross
                        pos_history.clear()
                    elif prev_inside and not curr_inside:
                        # Crossed outward: EXIT
                        evt = emit_event(STORE_ID, camera_id, visitor_id, "EXIT", current_time, is_staff=is_staff_member, confidence=confidence)
                        out_file.write(json.dumps(evt) + "\n")
                        tracker.register_exit(visitor_id, current_time, bbox)
                        pos_history.clear()
                        
            elif camera_id == "CAM_4": # Backroom
                # Backroom is a single zone
                zone_id = "BACKROOM"
                if visitor_id not in visitor_zones:
                    visitor_zones[visitor_id] = {}
                
                if zone_id not in visitor_zones[visitor_id]:
                    # Enter Backroom
                    visitor_zones[visitor_id][zone_id] = current_time
                    evt = emit_event(STORE_ID, camera_id, visitor_id, "ZONE_ENTER", current_time, zone_id=zone_id, is_staff=True, confidence=confidence)
                    out_file.write(json.dumps(evt) + "\n")
                    
                    visitor_dwells[visitor_id] = {
                        zone_id: {
                            "start_time": current_time,
                            "last_emit": current_time
                        }
                    }
                else:
                    # Dwell check (every 30s)
                    dwell_info = visitor_dwells.get(visitor_id, {}).get(zone_id)
                    if dwell_info:
                        elapsed = (current_time - dwell_info["start_time"]).total_seconds()
                        since_last_emit = (current_time - dwell_info["last_emit"]).total_seconds()
                        if since_last_emit >= 30.0:
                            evt = emit_event(STORE_ID, camera_id, visitor_id, "ZONE_DWELL", current_time, zone_id=zone_id, dwell_ms=elapsed*1000, is_staff=True, confidence=confidence)
                            out_file.write(json.dumps(evt) + "\n")
                            dwell_info["last_emit"] = current_time

            else: # Main Floor (CAM 1, CAM 2) and Billing (CAM 5)
                camera_polygons = ZONE_POLYGONS.get(camera_id, {})
                active_zone = None
                
                # Check polygon membership
                for z_id, poly in camera_polygons.items():
                    if is_point_in_polygon(norm_center, poly):
                        active_zone = z_id
                        break
                
                # Update zone states
                if visitor_id not in visitor_zones:
                    visitor_zones[visitor_id] = {}
                    
                # Handle exits from other zones
                for z_id in list(visitor_zones[visitor_id].keys()):
                    if z_id != active_zone:
                        enter_time = visitor_zones[visitor_id].pop(z_id)
                        dwell_ms = (current_time - enter_time).total_seconds() * 1000
                        evt = emit_event(STORE_ID, camera_id, visitor_id, "ZONE_EXIT", current_time, zone_id=z_id, dwell_ms=dwell_ms, is_staff=is_staff_member, confidence=confidence)
                        out_file.write(json.dumps(evt) + "\n")
                        
                        # If billing exit, check if it's an abandon (handled in API or emit helper)
                        if z_id == "BILLING" and not is_staff_member:
                            # We emit a BILLING_QUEUE_ABANDON. The API will correlate with POS transactions to confirm.
                            evt_ab = emit_event(STORE_ID, camera_id, visitor_id, "BILLING_QUEUE_ABANDON", current_time, zone_id="BILLING", is_staff=False, confidence=confidence)
                            out_file.write(json.dumps(evt_ab) + "\n")
                            if visitor_id in billing_customers:
                                billing_customers.remove(visitor_id)
                
                # Handle enters
                if active_zone and active_zone not in visitor_zones[visitor_id]:
                    visitor_zones[visitor_id][active_zone] = current_time
                    evt = emit_event(STORE_ID, camera_id, visitor_id, "ZONE_ENTER", current_time, zone_id=active_zone, is_staff=is_staff_member, confidence=confidence)
                    out_file.write(json.dumps(evt) + "\n")
                    
                    if visitor_id not in visitor_dwells:
                        visitor_dwells[visitor_id] = {}
                    visitor_dwells[visitor_id][active_zone] = {
                        "start_time": current_time,
                        "last_emit": current_time
                    }
                    
                    # Handle Billing Queue Join (CAM 5)
                    if active_zone == "BILLING" and not is_staff_member:
                        current_q_depth = len(billing_customers)
                        if current_q_depth > 0:
                            # Emit queue join
                            metadata = {"queue_depth": current_q_depth, "session_seq": len(pos_history) if 'pos_history' in locals() else 1}
                            evt_q = emit_event(STORE_ID, camera_id, visitor_id, "BILLING_QUEUE_JOIN", current_time, zone_id="BILLING", is_staff=False, confidence=confidence, metadata=metadata)
                            out_file.write(json.dumps(evt_q) + "\n")
                        billing_customers.add(visitor_id)
                        
                elif active_zone:
                    # Dwell check (every 30s)
                    dwell_info = visitor_dwells.get(visitor_id, {}).get(active_zone)
                    if dwell_info:
                        elapsed = (current_time - dwell_info["start_time"]).total_seconds()
                        since_last_emit = (current_time - dwell_info["last_emit"]).total_seconds()
                        if since_last_emit >= 30.0:
                            evt = emit_event(STORE_ID, camera_id, visitor_id, "ZONE_DWELL", current_time, zone_id=active_zone, dwell_ms=elapsed*1000, is_staff=is_staff_member, confidence=confidence)
                            out_file.write(json.dumps(evt) + "\n")
                            dwell_info["last_emit"] = current_time
                            
            if camera_id == "CAM_5" and active_zone == "BILLING" and not is_staff_member:
                current_billing_customers.add(visitor_id)

        # Track tracks that exited the frame or died
        active_tids = [int(t[4]) for t in tracks]
        for tid in list(tracker.active_tracks.keys()):
            if tid not in active_tids:
                # The track was lost
                vid = tracker.active_tracks.get(tid)
                if vid in visitor_zones:
                    # Clean up zones
                    for z_id in list(visitor_zones[vid].keys()):
                        enter_time = visitor_zones[vid].pop(z_id)
                        dwell_ms = (current_time - enter_time).total_seconds() * 1000
                        evt = emit_event(STORE_ID, camera_id, vid, "ZONE_EXIT", current_time, zone_id=z_id, dwell_ms=dwell_ms, is_staff=vid in tracker.staff_visitor_ids, confidence=1.0)
                        out_file.write(json.dumps(evt) + "\n")
                        if z_id == "BILLING" and vid in billing_customers:
                            billing_customers.remove(vid)
                            
        frame_idx += 1
        
    cap.release()
    out_file.close()
    print(f"Completed {camera_id}. Output appended to {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Retail CCTV Processing Pipeline")
    parser.add_argument("--video", type=str, required=True, help="Path to video file")
    parser.add_argument("--output", type=str, default="data/events.jsonl", help="Output path for events")
    parser.add_argument("--append", action="store_true", help="Append events to file")
    
    args = parser.parse_args()
    
    tracker = ReIDTracker()
    process_video(args.video, args.output, args.append, tracker)