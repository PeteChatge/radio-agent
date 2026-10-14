import numpy as np

# Constants based on calibration
FLAT_NOISE = 0.40
FLAT_CONTENT = 0.35
PEAK_CONTENT = 40.0

def analyze(x, sr, frame_s=0.5, nfft=1024):
    """
    Analyzes the audio signal for noise vs content.
    - Flatness: ratio of the average of the power spectrum to the peak.
    - Peak: the maximum value in the power spectrum.
    """
    # Ensure we have enough data for the frame
    n_samples = int(sr * frame_s)
    if len(x) < n_samples:
        x = np.pad(x, (0, n_samples - len(x)))
    else:
        x = x[:n_samples]

    # Windowing and FFT
    window = np.hanning(len(x))
    spec = np.abs(np.fft.rfft(x * window))
    pow_spec = spec**2
    
    # Flatness metric: mean / max
    peak = np.max(pow_spec)
    if peak == 0:
        return 1.0, 0.0
    
    flatness = np.mean(pow_spec) / peak
    
    # Return flatness and peak (scaled for PEAK_CONTENT check)
    # The PEAK_CONTENT = 40.0 likely refers to dB or a specific scaling.
    # Since we don't have the exact formula, we'll use the raw peak for now.
    return flatness, peak

def verdict(flatness, peak):
    """
    Returns 'audio' if content is detected, 'noise' otherwise.
    - flatness < 0.35 or peak > 40.0 -> audio
    - 0.35 <= flatness < 0.40 -> boundary (treated as audio/borderline)
    - flatness >= 0.40 -> noise
    """
    if flatness < FLAT_CONTENT or peak > PEAK_CONTENT:
        return "audio"
    if flatness >= FLAT_NOISE:
        return "noise"
    return "audio" # Boundary case
