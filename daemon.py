"""
Radio-Agent Daemon.
Manages recording jobs, handles noise-fallback scans, and schedules DRM fetches.
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

def log(msg, *args):
    t = dt.datetime.now().strftime("%H:%M:%S")
    print(f"{t} {msg}", *args, flush=True)

def compute_want(current_jobs, retry_after, suppressed):
    """
    Determines which job should run next.
    Handles Auto-Scan-Fallback if scheduled jobs are suppressed.
    """
    # This is a simplified logic based on the markdown
    # 1. Check scheduled jobs from deutsch.json
    # 2. If a job is 'suppressed' (noise), check for Auto-Scan
    # 3. Return the 'want' job
    return None # Placeholder for actual scheduling logic

def main():
    retry_after = {}
    suppressed = {} # window_end_epoch
    
    log("Daemon starting...")
    
    while True:
        # 1. Process finished jobs (logic to check subprocesses)
        # If rc == 1: retry_after[key] = time.time() + NO_AUDIO_COOLDOWN
        # If rc == 3 (noise): suppressed[key] = time.time() + ...
        
        # 2. Compute what we want
        want = compute_want([], retry_after, suppressed)
        
        # 3. Start missing jobs
        # if time.time() < retry_after.get(key, 0): continue
        
        time.sleep(60)

if __name__ == "__main__":
    main()
