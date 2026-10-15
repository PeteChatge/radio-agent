"""Gemeinsamer Rausch-/Signal-Detektor fuer Funkaufnahmen (numpy).

Ein Signal gilt als NUR-Rauschen, wenn das Spektrum breitbandig/flach ist
(kein tonales oder formantisches Signal) UND keine starke Spektralspitze
(Traeger/Whistle) vorhanden ist.

Kalibriert:
  weisses Rauschen        -> spektrale Flachheit ~0.56
  Sprache/Sender (AM/SSB) -> Flachheit < 0.35
  Flachheit 0.35..0.40    -> "borderline" (wird NICHT automatisch verworfen)

Benutzung:
  import rxdetect
  f = rxdetect.analyze(samples, sr, frame_s=0.5, nfft=1024)   # dict | None
  rxdetect.verdict(f)   # "noise" | "audio" | "borderline" | "error"
  rxdetect.is_noise(f)  # bool
"""
import numpy as np

FLAT_NOISE = 0.40     # >= -> breitbandig (Rauschen)
FLAT_CONTENT = 0.35   # <  -> tonales/formantisches Signal (Sender)
PEAK_CONTENT = 40.0   # >  -> starke Spektralspitze (Traeger) => Sender


def analyze(x, sr, frame_s=0.5, nfft=1024, max_seg=300, min_frames=4):
    """Spektrale/time-Statistik eines Float-Signals.

    x: 1-D Samples (float, +/-1.0). Rueckgabe: dict oder None.
    """
    x = np.asarray(x, dtype=np.float64)
    n = x.size
    if n < 64:
        return None
    frame = int(sr * frame_s)
    frame = max(64, min(frame, n))
    hop = max(1, frame // 2)
    nf = 1 + (n - frame) // hop
    if nf < min_frames:
        frame = max(64, n // (min_frames + 1))
        hop = max(1, frame // 2)
        nf = 1 + (n - frame) // hop

    idx = np.arange(frame)[None, :] + hop * np.arange(nf)[:, None]
    fr = x[idx]
    rms = np.sqrt(np.mean(fr * fr, axis=1) + 1e-20)
    db = 20 * np.log10(rms + 1e-12)
    p10, p90 = np.percentile(db, [10, 90])

    N = int(min(nfft, frame))
    win = np.hanning(N)
    step = max(1, nf // max_seg)
    flats, peaks = [], []
    for i in range(0, nf, step):
        seg = x[i * hop:i * hop + N]
        if seg.size < N:
            break
        P = np.abs(np.fft.rfft(seg * win)) ** 2 + 1e-20
        P = P[1:]
        if P.size < 8:
            continue
        flats.append(np.exp(np.mean(np.log(P))) / np.mean(P))
        peaks.append(P.max() / np.mean(P))
    if not flats:
        return None
    return {
        "dur_s": round(n / sr, 2),
        "mean_db": round(float(np.mean(db)), 1),
        "lra_db": round(float(p90 - p10), 1),
        "flatness": round(float(np.median(flats)), 4),
        "flat_p25": round(float(np.percentile(flats, 25)), 4),
        "peakbin": round(float(np.median(peaks)), 1),
    }


def verdict(f):
    if f is None:
        return "error"
    if f["flatness"] < FLAT_CONTENT or f["peakbin"] > PEAK_CONTENT:
        return "audio"
    if f["flatness"] >= FLAT_NOISE:
        return "noise"
    return "borderline"


def is_noise(f):
    return verdict(f) == "noise"
