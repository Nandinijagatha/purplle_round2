import os
import subprocess
import sys

def main():
    # CCTV footage directory
    video_dir = video_dir = os.path.join(os.path.expanduser("~"), "Downloads", "CCTV Footage-20260529T160731Z-3-00144614ea(1)", "CCTV Footage")
    output_events = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "events.jsonl")
    # Clean output file first
    if os.path.exists(output_events):
        os.remove(output_events)
        print(f"Removed old events file: {output_events}")
        
    # Ensure data dir exists
    os.makedirs(os.path.dirname(output_events), exist_ok=True)
    
    # Process CAM 1 through CAM 5
    for i in range(1, 6):
        video_path = os.path.join(video_dir, f"CAM {i}.mp4")
        if os.path.exists(video_path):
            print(f"\n--- Batch processing clip {i}/5: {os.path.basename(video_path)} ---")
            cmd = [
                sys.executable,
                "detect.py",
                "--video", video_path,
                "--output", output_events,
                "--append"
            ]
            # Run from pipeline directory
            result = subprocess.run(cmd, cwd=os.path.dirname(__file__))
            if result.returncode != 0:
                print(f"Error processing CAM {i}. Return code: {result.returncode}")
        else:
            print(f"Video clip not found: {video_path}")
            
    print(f"\nBatch processing finished. Events written to: {output_events}")

if __name__ == "__main__":
    main()
