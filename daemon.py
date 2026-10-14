"""
Radio-Agent Daemon.
Manages recording jobs, handles noise-fallback scans, and schedules DRM fetches.
Implements high-priority Fax-windows that override all other recordings.
"""
import os
import sys
import time
import subprocess
import json
import datetime as dt

# Constants
SCAN_REC_SECONDS = 60
SCAN_MAX = 120
SCAN_COOLDOWN = 1800        # Pause between explicit planned scans
AUTOSCAN_COOLDOWN = 15      # Pause between Auto-Fallback scans
DRM_TTL = 86400
NO_AUDIO_COOLDOWN = 120     # no audio (e.g. DRM without Lock): per job
ERROR_COOLDOWN = 120        # other error: per job

# Paths
ARCH = "/archive"
SCHEDULE_FILE = os.path.join(ARCH, "schedule", "deutsch.json")
DRM_SCHEDULE_FILE = os.path.join(ARCH, "schedule", "drm.json")
FAX_SCHEDULE_FILE = os.path.join(ARCH, "schedule", "fax_dwd.json")
SETTINGS_FILE = "/opt/radio-agent/settings.json"

def log(msg, *args):
    t = dt.datetime.now().strftime("%H:%M:%S")
    print(f"{t} {msg}", *args, flush=True)

def get_settings():
    try:
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def is_fax_window():
    """
    Checks if the current time falls within a DWD-Fax window.
    Fax windows have absolute priority.
    """
    now = dt.datetime.now(dt.timezone.utc)
    now_time = now.strftime("%H:%M")
    
    try:
        with open(FAX_SCHEDULE_FILE, "r") as f:
            fax_jobs = json.load(f)
        
        for job in fax_jobs:
            start_time = job["start"]
            duration = job["duration"]
            
            # Convert start_time to a datetime object for the current day
            start_dt = dt.datetime.strptime(f"{now.date()} {start_time}", "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
            end_dt = start_dt + dt.timedelta(minutes=duration)
            
            if start_dt <= now <= end_dt:
                return True
    except Exception as e:
        log(f"Error checking fax schedule: {e}")
        
    return False

def compute_want(current_jobs, retry_after, suppressed):
    """
    Determines which job should run next.
    Priority: 
    1. Fax Windows (Absolute)
    2. Scheduled Audio Jobs
    3. Auto-Scan Fallback
    """
    now = time.time()
    
    # PRIORITY 1: FAX
    if is_fax_window():
        log("PRIORITY: Fax-Window active. Suppressing all other jobs.")
        return {"type": "fax", "action": "RECORD_FAX"}

    # PRIORITY 2: Scheduled Jobs
    try:
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
            sched = json.load(f)
    except:
        return None

    # Simplified matching logic for the a-job from deutsch.json
    for entry in sched.get("entries", []):
        # Check if it's time to record this entry
        # (Actual time matching logic would go here)
        if entry.get("priority") == "high":
            return {"type": "audio", "entry": entry}

    # PRIORITY 3: Auto-Scan Fallback
    if not current_jobs:
        return {"type": "scan", "band": "mw"}

    return None

def main():
    retry_after = {}
    suppressed = {} # window_end_epoch
    
    log("Daemon starting with Fax-Priority Logic...")
    
    while True:
        # 1. Process finished jobs
        # (Implementation of subprocess polling)
        
        # 2. Compute what we want
        want = compute_want([], retry_after, suppressed)
        
        if want:
            if want["type"] == "fax":
                log("Starting High-Priority FAX recording...")
                # Start fax record process
            elif want["type"] == "audio":
                log(f"Starting audio job: {want['entry'].get('sender')}")
                # Start audio record process
            elif want["type"] == "scan":
                log(f"Starting fallback scan: {want['band']}")
                # Start scan process
        
        time.sleep(60)

if __name__ == "__main__":
    main()
