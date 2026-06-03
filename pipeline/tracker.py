import cv2
import numpy as np
import uuid

class ReIDTracker:
    def __init__(self):
        # Maps track_id -> visitor_id
        self.active_tracks = {}
        # Stores history of exited visitors: list of dicts {"visitor_id": id, "exit_time": timestamp, "avg_box_width": w}
        self.exited_signatures = []
        # Staff visitor IDs (once identified as staff, keep them flagged as staff)
        self.staff_visitor_ids = set()

    def get_visitor_id(self, track_id, bbox, timestamp, camera_id=None):
        """
        Retrieves or generates a unique visitor_id for a given tracker track_id.
        """
        # If already tracked, return existing visitor_id
        if track_id in self.active_tracks:
            v_id = self.active_tracks[track_id]
            is_staff = v_id in self.staff_visitor_ids
            return v_id, "NONE", is_staff

        # For a new track_id, check if it matches a recently exited visitor (Re-entry handling)
        # Check within a 5-minute window (300 seconds)
        matched_vid = None
        box_w = bbox[2] - bbox[0]
        
        # Sort exited signatures by exit_time descending (newest first)
        self.exited_signatures.sort(key=lambda x: x["exit_time"], reverse=True)
        
        for sig in self.exited_signatures:
            time_diff = (timestamp - sig["exit_time"]).total_seconds()
            if 0 <= time_diff <= 300:
                # Match by similar bounding box width as a heuristic (within 25% difference)
                width_diff = abs(sig["avg_box_width"] - box_w) / max(sig["avg_box_width"], box_w, 1)
                if width_diff < 0.25:
                    matched_vid = sig["visitor_id"]
                    # Remove from exited signatures as they have re-entered
                    self.exited_signatures.remove(sig)
                    break
            elif time_diff > 300:
                # Clean up old signatures
                self.exited_signatures.remove(sig)

        if matched_vid:
            self.active_tracks[track_id] = matched_vid
            is_staff = matched_vid in self.staff_visitor_ids
            return matched_vid, "REENTRY", is_staff
        
        # Create a new visitor
        new_vid = f"VIS_{uuid.uuid4().hex[:6].upper()}"
        self.active_tracks[track_id] = new_vid
        return new_vid, "ENTRY", False

    def register_exit(self, visitor_id, timestamp, bbox):
        """
        Registers when a visitor exits the store, storing their signature for re-entry matching.
        """
        box_w = bbox[2] - bbox[0]
        self.exited_signatures.append({
            "visitor_id": visitor_id,
            "exit_time": timestamp,
            "avg_box_width": box_w
        })
        # Remove from active tracks to free space
        for tid, vid in list(self.active_tracks.items()):
            if vid == visitor_id:
                del self.active_tracks[tid]

    def classify_staff(self, frame, bbox, camera_id, norm_center):
        """
        Determines whether a tracked person is a staff member based on:
        1. Camera context (CAM 4 is the staff backroom, anyone there is staff).
        2. Position (Cashier zone behind the counter in CAM 5).
        3. Color profile of uniform (black shirt).
        """
        # Rule 1: Backroom is staff-only
        if camera_id == "CAM_4":
            return True

        # Rule 2: Cashier desk area in CAM 5
        # cashier desk is in the bottom-left area of CAM 5 (norm_center: x < 0.35, y > 0.4)
        if camera_id == "CAM_5":
            cx, cy = norm_center
            if cx < 0.35 and cy > 0.4:
                return True

        # Rule 3: Torso color histogram (black uniform detection)
        # Extract the chest region (approx. top 20% to 50% of the bounding box height, 25% to 75% of width)
        try:
            h, w, _ = frame.shape
            x1, y1, x2, y2 = map(int, bbox)
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(w, x2)
            y2 = min(h, y2)
            
            box_h = y2 - y1
            box_w = x2 - x1
            if box_h > 30 and box_w > 20:
                chest_y1 = y1 + int(box_h * 0.2)
                chest_y2 = y1 + int(box_h * 0.5)
                chest_x1 = x1 + int(box_w * 0.25)
                chest_x2 = x1 + int(box_w * 0.75)
                
                chest_crop = frame[chest_y1:chest_y2, chest_x1:chest_x2]
                if chest_crop.size > 0:
                    hsv = cv2.cvtColor(chest_crop, cv2.COLOR_BGR2HSV)
                    # Define black uniform range in HSV
                    # Low brightness (V < 60), any hue, low saturation
                    lower_black = np.array([0, 0, 0])
                    upper_black = np.array([180, 255, 65])
                    mask = cv2.inRange(hsv, lower_black, upper_black)
                    black_ratio = np.sum(mask > 0) / mask.size
                    if black_ratio > 0.45: # Over 45% of the chest is black
                        return True
        except Exception:
            pass

        return False

    def mark_as_staff(self, visitor_id):
        self.staff_visitor_ids.add(visitor_id)