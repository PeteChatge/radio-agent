import numpy as np
from rxdetect import analyze, verdict

def check_noise(samples, sr):
    """
    Analyzes a block of samples and returns True if it is noise, False otherwise.
    """
    flatness, peak = analyze(samples, sr)
    return verdict(flatness, peak) == "noise"

if __name__ == "__main__":
    # Basic test if run directly
    import sys
    # Expects binary data on stdin
    data = sys.stdin.buffer.read()
    if not data:
        print("No data")
        sys.exit(1)
    
    # Assume 16-bit PCM
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    sr = 12000 # Default OWRX rate
    
    if check_noise(samples, sr):
        print("noise")
        sys.exit(0)
    else:
        print("audio")
        sys.exit(0)
