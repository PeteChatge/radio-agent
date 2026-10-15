"""Ganzband-Scanner (Ticket: Scan-Modus).

Stimmt ein ganzes Band in bandueblichen Schritten durch, verweilt je Kanal
kurz (Default 0.25 s) und entscheidet per Rausch-Gate (rxdetect), ob ein
Sender vorhanden ist. Nur belegte Kanaele werden aufgezeichnet; reine
Rauschkanaele erzeugen KEINE Datei.

Banduebliche Schritte:
  * MW (Mittelwelle): 9 kHz   (531..1602 kHz)
  * KW (Kurzweite):   10 kHz   (1.5..20.4 MHz ueber Scan-Profile)

  * Probelauf/Dwell: Noise-Reduction AUS (kalibrierter Rauschdetektor)
  * Aufnahme belegter Kanaele: Noise-Reduction AN (moderat +5)

Aufruf:
  scan.py mw  [--dwell 0.25] [--record-seconds 60] [--max 200] [--dry-run]
  scan.py sw  [--profiles scan2,scan3,...] [--record-seconds 90] ...
  scan.py all [...]

Ausgabe: /archive/scan/<band>/<ts>_<freq>kHz_<mod>.mp3 (+ .json)
Report:  /archive/scan/scan-report-<ts>.json
Sperre:  /tmp/scan.lock
"""
import argparse
import asyncio
import datetime as dt
import json
import os
import re
import struct
import subprocess
import sys
import time
import wave

import numpy as np
import websockets

import rxdetect
from record import ImaDecoder, profile_cfg

RATE = 12000
URL = "ws://localhost:8073/ws/"
ARCH = "/archive"
SCAN_ROOT = os.path.join(ARCH, "scan")
LOCK = "/tmp/scan.lock"
GUARD = 50000          # Randabstand zum Filterrand (Hz)
SETTLE = 0.15          # Einschwingzeit nach Offset-Wechsel (s)
NR_THRESHOLD = 5
FREQ_RE = re.compile(r"_(\d+)kHz_")


def load_recent(band, minutes):
    """Neueste Aufnahmezeit je Frequenz (kHz) der letzten <minutes> Minuten."""
    idx = {}
    if minutes <= 0:
        return idx
    d = os.path.join(SCAN_ROOT, band)
    try:
        names = os.listdir(d)
    except OSError:
        return idx
    cutoff = time.time() - minutes * 60
    for n in names:
        m = FREQ_RE.search(n)
        if not m:
            continue
        try:
            mt = os.stat(os.path.join(d, n)).st_mtime
        except OSError:
            continue
        if mt >= cutoff:
            f = int(m.group(1)) * 1000
            idx[f] = max(idx.get(f, 0.0), mt)
    return idx

MW = {"band": "mw", "mod": "am", "step": 9000, "lo": 531000, "hi": 1602000,
      "profiles": [("sdrplay|scanmw", 1066500, 2000000)]}
SW_PROFILES = ["scan2", "scan3", "scan4", "scan5", "scan6",
               "scan7", "scan8", "scan9", "scan10"]


def channels_for(profiles, step, lo, hi):
    """Kanal-Liste (Hz) fuer eine Folge von Profilen, auf step-Raster."""
    out = []
    for pid, center, samp in profiles:
        half = samp // 2 - GUARD
        a = max(lo, center - half)
        b = min(hi, center + half)
        f = ((a + step - 1) // step) * step
        while f <= b:
            out.append((pid, f))
            f += step
    return out


def band_plan(band):
    if band == "mw":
        return MW
    if band == "sw":
        profs = [(f"sdrplay|{p}", *profile_cfg(f"sdrplay|{p}")) for p in SW_PROFILES]
        return {"band": "sw", "mod": "am", "step": 10000, "lo": 1500000,
                "hi": 20400000, "profiles": profs}
    raise ValueError(band)


async def wait_profiles(ws):
    async with asyncio.timeout(15):
        async for msg in ws:
            if isinstance(msg, str):
                try:
                    d = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                if d.get("type") == "profiles":
                    return


def dsp_params(offset, mod, nr_enabled):
    return {"low_cut": -4000, "high_cut": 4000, "offset_freq": offset,
            "mod": mod, "squelch_level": -150, "audio_service_id": -1,
            "dmr_filter": 0, "secondary_mod": "off", "secondary_offset_freq": 0,
            "nr_enabled": bool(nr_enabled), "nr_threshold": NR_THRESHOLD}


async def capture(ws, seconds):
    dec = ImaDecoder()
    out: list = []
    end = time.monotonic() + seconds
    while True:
        rem = end - time.monotonic()
        if rem <= 0:
            break
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=min(rem, 1.0))
        except asyncio.TimeoutError:
            continue
        if isinstance(msg, bytes) and len(msg) > 1 and msg[0] == 0x02:
            dec.feed(msg[1:], out)
    return out


async def capture_dwell(ws, seconds):
    """Sammelt mind. seconds*RATE Samples (ueberbrueckt die Neu-Tune-Luecke)."""
    need = int(seconds * RATE)
    dec = ImaDecoder()
    out: list = []
    end = time.monotonic() + seconds + 0.8
    while len(out) < need and time.monotonic() < end:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        if isinstance(msg, bytes) and len(msg) > 1 and msg[0] == 0x02:
            dec.feed(msg[1:], out)
    return out


def to_float(samples):
    if not samples:
        return None
    return np.asarray(samples, dtype=np.float64) / 32768.0


def save(samples, out_mp3):
    pcm = struct.pack(f"<{len(samples)}h",
                      *[min(max(int(s), -32768), 32767) for s in samples])
    wav = out_mp3 + ".wav"
    os.makedirs(os.path.dirname(out_mp3), exist_ok=True)
    with wave.open(wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", wav,
                    "-ar", "12000", "-ac", "1", "-b:a", "64k", out_mp3], check=True)
    try:
        os.remove(wav)
    except OSError:
        pass


async def scan_band(plan, args):
    step, mod = plan["step"], plan["mod"]
    chans = channels_for(plan["profiles"], step, plan["lo"], plan["hi"])
    print(f"BAND {plan['band']} step={step}Hz kanaele={len(chans)} "
          f"dwell={args.dwell}s rec={args.record_seconds}s", flush=True)
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    report = {"band": plan["band"], "step_hz": step, "started": ts,
              "dwell_s": args.dwell, "record_seconds": args.record_seconds,
              "hits": [], "scanned": 0, "deduped": 0}
    nrec = 0
    recent = load_recent(plan["band"], args.dedup_minutes)
    if recent:
        print(f"  DEDUP {len(recent)} Frequenzen <{args.dedup_minutes}min (werden uebersprungen)",
              flush=True)
    for pid, center, samp in plan["profiles"]:
        # nur Kanaele dieses Profils
        mine = [(p, f) for (p, f) in chans if p == pid]
        if not mine:
            continue
        async with websockets.connect(URL, max_size=None, ping_interval=None) as ws:
            await ws.send("SERVER DE CLIENT client=openwebrx.js type=receiver")
            await ws.send(json.dumps({"type": "connectionproperties",
                                      "params": {"output_rate": 12000, "hd_output_rate": 48000}}))
            await wait_profiles(ws)
            await asyncio.sleep(2)
            await ws.send(json.dumps({"type": "selectprofile",
                                      "params": {"profile": pid}}))
            await asyncio.sleep(2)
            await ws.send(json.dumps({"type": "dspcontrol",
                                      "action": "start"}))
            await asyncio.sleep(1.5)
            print(f"  PROFIL {pid} center={center} kanaele={len(mine)}", flush=True)
            for _pid, f in mine:
                if args.max and nrec >= args.max:
                    print("  MAX Aufnahmen erreicht – Abbruch", flush=True)
                    break
                offset = f - center
                await ws.send(json.dumps({"type": "dspcontrol",
                                          "params": dsp_params(offset, mod, False)}))
                await asyncio.sleep(SETTLE)
                dwell = await capture_dwell(ws, args.dwell)
                feat = rxdetect.analyze(to_float(dwell), RATE, frame_s=0.25, nfft=512)
                v = rxdetect.verdict(feat)
                report["scanned"] += 1
                if v in ("audio", "borderline"):
                    if args.dedup_minutes and f in recent and \
                            time.time() - recent[f] < args.dedup_minutes * 60:
                        report["deduped"] += 1
                        if args.verbose:
                            print(f"    = {f/1000:.0f}kHz {v} (schon kuerzlich aufgenommen)",
                                  flush=True)
                        continue
                    nrec += 1
                    rec = await capture(ws, args.record_seconds) if not args.dry_run else []
                    if args.dry_run:
                        print(f"    HIT {f/1000:.0f}kHz {v} flat={feat.get('flatness')} "
                              f"(dry-run)", flush=True)
                    else:
                        rec_feat = rxdetect.analyze(to_float(rec), RATE, frame_s=0.5, nfft=1024)
                        fts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M")
                        sdir = os.path.join(SCAN_ROOT, plan["band"])
                        out = os.path.join(sdir, f"{fts}_{f//1000}kHz_{mod}.mp3")
                        save(rec, out)
                        recent[f] = time.time()
                        with open(out + ".json", "w", encoding="utf-8") as fh:
                            json.dump({"band": plan["band"], "freqHz": f, "mode": mod,
                                       "profil": pid, "dwell_verdict": v,
                                       "dwell_feat": feat, "rec_feat": rec_feat,
                                       "rec_seconds": args.record_seconds,
                                       "nr_threshold": NR_THRESHOLD,
                                       "scanned_utc": dt.datetime.now(dt.timezone.utc).isoformat()},
                                      fh, ensure_ascii=False, indent=1)
                        print(f"    REC {f/1000:.0f}kHz {v} flat={feat.get('flatness')} -> "
                              f"{os.path.basename(out)}", flush=True)
                    report["hits"].append({"freqHz": f, "verdict": v,
                                           "flatness": (feat or {}).get("flatness")})
                elif v == "noise":
                    if args.verbose:
                        print(f"    - {f/1000:.0f}kHz Rauschen flat="
                              f"{(feat or {}).get('flatness')}", flush=True)
        if args.max and nrec >= args.max:
            break
    report["finished"] = dt.datetime.now(dt.timezone.utc).isoformat()
    os.makedirs(SCAN_ROOT, exist_ok=True)
    rep = os.path.join(SCAN_ROOT, f"scan-report-{ts}.json")
    with open(rep, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    print(f"FERTIG {plan['band']}: {len(report['hits'])} Treffer / "
          f"{report['scanned']} kanaele ({report['deduped']} dedup) -> {rep}", flush=True)
    return report


async def amain(args):
    plans = [band_plan("mw"), band_plan("sw")] if args.band == "all" \
        else [band_plan(args.band)]
    for plan in plans:
        await scan_band(plan, args)


def main():
    ap = argparse.ArgumentParser(description="Ganzband-Rausch-/Sender-Scanner")
    ap.add_argument("band", choices=["mw", "sw", "all"])
    ap.add_argument("--dwell", type=float, default=0.25)
    ap.add_argument("--record-seconds", type=float, default=60.0)
    ap.add_argument("--max", type=int, default=0, help="max. Aufnahmen (0=unbegrenzt)")
    ap.add_argument("--dedup-minutes", type=int, default=30,
                    help="Frequenz nicht erneut aufnehmen, wenn in den letzten N min schon (0=aus)")
    ap.add_argument("--dry-run", action="store_true", help="nur Treffer loggen, nichts speichern")
    ap.add_argument("--verbose", action="store_true", help="auch Rauschkanaele loggen")
    args = ap.parse_args()
    fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    try:
        asyncio.run(amain(args))
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    main()
