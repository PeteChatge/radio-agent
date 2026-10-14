"""
DRM Schedule Fetcher.
Downloads the latest schedule from drm.org and saves it to /archive/schedule/drm.json.
"""
import json
import os
import urllib.request

SCHEDULE_URL = "https://www.drm.org/schedule/" # Example URL, adjust as needed
DEST_PATH = "/archive/schedule/drm.json"

def fetch_drm_schedule():
    print(f"Fetching DRM schedule from {SCHEDULE_URL}...")
    try:
        # In a real scenario, we might need to parse HTML or hit a specific JSON endpoint.
        # Since drm.org usually provides HTML, we'd use a parser.
        # For this implementation, we assume we are fetching a JSON or simulating the save.
        with urllib.request.urlopen(SCHEDULE_URL, timeout=30) as response:
            data = response.read()
            
        # Ensure directory exists
        os.makedirs(os.path.dirname(DEST_PATH), exist_ok=True)
        
        with open(DEST_PATH, "wb") as f:
            f.write(data)
            
        print(f"Schedule saved to {DEST_PATH}")
        return True
    except Exception as e:
        print(f"Error fetching DRM schedule: {e}")
        return False

if __name__ == "__main__":
    fetch_drm_schedule()
