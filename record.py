"""OWRX headless recorder v2.

Tunt per WS (selectprofile + dspcontrol), sammelt 0x02-Frames (IMA-ADPCM),
schreibt WAV (12 kHz mono) + mp3 (64k).

Rausch-Gate: Zuerst kurzer Probelauf mit Noise-Reduction AUS. Enthaelt der
Abschnitt NUR Rauschen (rxdetect), wird KEINE Datei geschrieben und das
Programm beendet sich mit Exit-Code 3 ("verworfen"). Sonst laeuft die
restliche Aufnahme mit Noise-Reduction AN (Standard: moderat +5).

Aufruf: record.py <profile-id> <freqHz> <mod> <sekunden> <out.mp3>
Bsp:     record.py sdrplay|am 792000 am 60 boehmen.mp3

ENV:  PROBE_SECONDS (Default 0.4), NR_ENABLED (1/0), NR_THRESHOLD (Default 5),
      DRM_LOCK_SECONDS (Default 90)
"""
import asyncio
import json
import os
import struct
import subprocess
import sys
import time
import wave

import numpy as np
import websockets

import rxdetect

DRM_LOCK_SECONDS = int(os.environ.get("DRM_LOCK_SECONDS", "90"))
INDEX_TABLE = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]
STEP_TABLE = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37,
    41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173,
    190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658,
    724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
    2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484,
    7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500,
    20350, 22385, 24623, 27086, 29794, 32767,
]
SYNC = b"SYNC"
RATE = 12000
URL = "ws://localhost:8073/ws/"
EXIT_NOISE = 3


def profile_cfg(profile):
    """Center/Samp aus lokaler Tabelle (WS-Config kommt nach Select unzuverlaessig)."""
    table = {
        "rtlsdr|70cm": (438800000, 2400000), "rtlsdr|2m": (145000000, 2048000),
        "rtlsdr|10m": (28000000, 2048000), "rtlsdr|40m": (7500000, 1024000),
        "rtlsdr|80m": (3500000, 1024000), "rtlsdr|160m": (1500000, 1024000),
        "rtlsdr|am": (1000000, 1024000), "rtlsdr|adsb1090": (1090000000, 2400000),
        "rtlsdr|mw": (700000, 1024000),
        "rtlsdr|31m": (9650000, 1024000), "rtlsdr|49m": (6050000, 1024000),
        "sdrplay|70cm": (443500000, 4000000), "sdrplay|125cm": (223500000, 4000000),
        "sdrplay|2m": (146000000, 4000000), "sdrplay|6m": (52000000, 4000000),
        "sdrplay|10m": (28850000, 2000000), "sdrplay|cb": (27000000, 2000000),
        "sdrplay|12m": (24940000, 250000), "sdrplay|15m": (21225000, 500000),
        "sdrplay|17m": (18118000, 250000), "sdrplay|20m": (14175000, 500000),
        "sdrplay|30m": (10125000, 250000), "sdrplay|40m": (7150000, 500000),
        "sdrplay|60m": (5350000, 250000), "sdrplay|80m": (3750000, 500000),
        "sdrplay|160m": (1900000, 250000), "sdrplay|am": (1100000, 1000000),
        "sdrplay|49m": (6050000, 500000), "sdrplay|41m": (7325000, 250000),
        "sdrplay|31m": (9650000, 500000), "sdrplay|25m": (11850000, 500000),
        "sdrplay|22m": (13720000, 250000), "sdrplay|ism433": (433000000, 2000000),
        "sdrplay|75m": (3975000, 250000), "sdrplay|mw": (800000, 1000000),
        "sdrplay|lw": (200000, 250000),
        "sdrplay|scanmw": (1066500, 2000000),
        "sdrplay|scan2": (2500000, 2000000), "sdrplay|scan3": (4900000, 2000000),
        "sdrplay|scan4": (6900000, 2000000), "sdrplay|scan5": (8900000, 2000000),
        "sdrplay|scan6": (11500000, 2000000), "sdrplay|scan7": (13500000, 2000000),
        "sdrplay|scan8": (15900000, 2000000), "sdrplay|scan9": (17900000, 2000000),
        "sdrplay|scan10": (19400000, 2000000),
        "sdrplay|adsb1090": (1090000000, 4000000),
        "sdrplay|fax80": (3900000, 1000000), "sdrplay|fax49": (7900000, 1000000),
    }
    if profile not in table:
        raise KeyError(f"Profil {profile} nicht in lokaler Tabelle")
    return table[profile]


class ImaDecoder:
    """Port von AudioEngine.js ImaAdpcmCodec.decodeWithSync."""

    def __init__(self):
        self.step_index = 0
        self.predictor = 0
        self.step = 0
        self.phase = 0
        self.synced = 0
        self.syncbuf = bytearray()
        self.sync_left = 0

    def nibble(self, n):
        self.step_index += INDEX_TABLE[n]
        self.step_index = min(max(self.step_index, 0), 88)
        diff = self.step >> 3
        if n & 1:
            diff += self.step >> 2
        if n & 2:
            diff += self.step >> 1
        if n & 4:
            diff += self.step
        if n & 8:
            diff = -diff
        self.predictor = min(max(self.predictor + diff, -32768), 32767)
        self.step = STEP_TABLE[self.step_index]
        return self.predictor

    def feed(self, data: bytes, out: list):
        for b in data:
            if self.phase == 0:
                if b != SYNC[self.synced]:
                    self.synced = 0
                    if b == SYNC[0]:
                        self.synced = 1
                else:
                    self.synced += 1
                    if self.synced == 4:
                        self.syncbuf = bytearray()
                        self.phase = 1
            elif self.phase == 1:
                self.syncbuf.append(b)
                if len(self.syncbuf) == 4:
                    si, pr = struct.unpack("<2h", bytes(self.syncbuf))
                    self.step_index = min(max(si, 0), 88)
                    self.predictor = pr
                    self.step = STEP_TABLE[self.step_index]
                    self.sync_left = 1000
                    self.phase = 2
            else:
                out.append(self.nibble(b & 0x0F))
                out.append(self.nibble((b >> 4) & 0x0F))
                self.sync_left -= 1
                if self.sync_left <= 0:
                    self.synced = 0
                    self.phase = 0


async def capture(ws, deadline, abort_after_no_audio=45):
    """Sammelt 0x02-Frames bis deadline (monotonic). Rueckgabe (samples, smeter, n_audio, n_other, aborted)."""
    dec = ImaDecoder()
    samples: list = []
    smeter: list = []
    n_audio = n_other = 0
    t0 = time.monotonic()
    aborted = False
    while True:
        rem = deadline - time.monotonic()
        if rem <= 0:
            break
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=min(rem, 2.0))
        except asyncio.TimeoutError:
            if time.monotonic() >= deadline:
                break
            continue
        if isinstance(msg, bytes) and len(msg) > 1 and msg[0] == 0x02:
            n_audio += 1
            dec.feed(msg[1:], samples)
        else:
            n_other += 1
            if isinstance(msg, str):
                try:
                    dd = json.loads(msg)
                except json.JSONDecodeError:
                    dd = None
                if dd and dd.get("type") == "smeter":
                    try:
                        smeter.append(float(dd.get("value", 0)))
                    except (TypeError, ValueError):
                        pass
            if n_audio == 0 and time.monotonic() - t0 > abort_after_no_audio:
                aborted = True
                break
    return samples, smeter, n_audio, n_other, aborted


def dsp_params(profile, freq, mod, nr_enabled, nr_threshold):
    center, samp = profile_cfg(profile)
    offset = freq - center
    assert abs(offset) <= samp / 2, f"Freq {freq} ausserhalb Profil (offset {offset}, bw {samp})"
    low, high = (-5000, 5000) if mod == "drm" else (-4000, 4000)
    return {
        "low_cut": low, "high_cut": high, "offset_freq": offset,
        "mod": mod, "squelch_level": -150, "audio_service_id": -1,
        "dmr_filter": 0, "secondary_mod": "off", "secondary_offset_freq": 0,
        "nr_enabled": bool(nr_enabled), "nr_threshold": int(nr_threshold),
    }


def analyze_samples(samples):
    if not samples:
        return None
    x = np.frombuffer(struct.pack(f"<{len(samples)}h",
                                  *[min(max(int(s), -32768), 32767) for s in samples]),
                      dtype="<i2").astype(np.float64) / 32768.0
    return rxdetect.analyze(x, RATE, frame_s=0.5, nfft=1024)


async def main():
    profile, freq, mod, secs, out_mp3 = (
        sys.argv[1], int(sys.argv[2]), sys.argv[3], float(sys.argv[4]), sys.argv[5])
    probe_s = float(os.environ.get("PROBE_SECONDS", "0.4"))
    nr_enabled = os.environ.get("NR_ENABLED", "1") != "0"
    nr_threshold = int(os.environ.get("NR_THRESHOLD", "5"))
    async with websockets.connect(URL, max_size=None, ping_interval=None) as ws:
        await ws.send("SERVER DE CLIENT client=openwebrx.js type=receiver")
        await ws.send(json.dumps({"type": "connectionproperties",
                                  "params": {"output_rate": 12000, "hd_output_rate": 48000}}))
        async with asyncio.timeout(15):
            async for msg in ws:
                if isinstance(msg, str):
                    try:
                        d = json.loads(msg)
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") == "profiles":
                        break
        await asyncio.sleep(3)
        await ws.send(json.dumps({"type": "selectprofile", "params": {"profile": profile}}))
        await asyncio.sleep(2)
        center, samp = profile_cfg(profile)
        print(f"PROFILE {profile} center={center} samp={samp}", flush=True)
        await asyncio.sleep(3)
        t_start = time.monotonic()
        deadline = t_start + secs

        p_samples = []
        p_audio = 0
        if mod == "drm":
            # DRM braucht den Dream-Decoder; ohne Lock gibt es kein Audio und
            # das Rausch-Gate ist nicht anwendbar -> direkt mit NR aufnehmen.
            await ws.send(json.dumps({"type": "dspcontrol",
                                      "params": dsp_params(profile, freq, mod, nr_enabled, nr_threshold)}))
            await ws.send(json.dumps({"type": "dspcontrol", "action": "start"}))
            drm_deadline = max(deadline, time.monotonic() + DRM_LOCK_SECONDS)
            samples, smeter, n_audio, n_other, aborted = await capture(
                ws, drm_deadline, abort_after_no_audio=DRM_LOCK_SECONDS)
            n_audio += p_audio
            samples = p_samples + samples
            if aborted:
                print(f"DRM-KEIN-LOCK-{DRM_LOCK_SECONDS}s – Abbruch", flush=True)
                return 1
        else:
            # 1) Probelauf mit Noise-Reduction AUS -> Rausch-Gate
            await ws.send(json.dumps({"type": "dspcontrol",
                                      "params": dsp_params(profile, freq, mod, False, 0)}))
            await ws.send(json.dumps({"type": "dspcontrol", "action": "start"}))
            probe_deadline = min(deadline, time.monotonic() + probe_s)
            p_samples, _p_sm, p_audio, _p_other, p_abort = await capture(
                ws, probe_deadline, abort_after_no_audio=45)
            feat = analyze_samples(p_samples)
            v = rxdetect.verdict(feat)
            print(f"PROBE {v} {feat}", flush=True)
            if v == "noise":
                print("RAUSCHEN – Aufnahme verworfen (keine Datei)", flush=True)
                return EXIT_NOISE

            # 2) Restliche Aufnahme mit Noise-Reduction AN
            await ws.send(json.dumps({"type": "dspcontrol",
                                      "params": dsp_params(profile, freq, mod, nr_enabled, nr_threshold)}))
            samples, smeter, n_audio, n_other, aborted = await capture(ws, deadline)
            n_audio += p_audio
            samples = p_samples + samples
            if aborted:
                print("KEIN-AUDIO-45s – Abbruch (Profilwechsel prüfen)", flush=True)
                return 1
        print(f"FRAMES audio={n_audio} andere={n_other} samples={len(samples)}", flush=True)
        if not samples:
            print("KEIN-AUDIO – Abbruch", flush=True)
            return 1
        pcm = struct.pack(f"<{len(samples)}h", *[min(max(int(s), -32768), 32767) for s in samples])
        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
        print(f"RMS={rms:.0f} dauer={len(samples) / RATE:.1f}s", flush=True)
        wav = out_mp3 + ".wav"
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", wav,
                        "-ar", "12000", "-ac", "1", "-b:a", "64k", out_mp3], check=True)
        smax = max(smeter) if smeter else 0.0
        savg = sum(smeter) / len(smeter) if smeter else 0.0
        print(f"FERTIG {out_mp3} SMETER avg={savg:.2e} max={smax:.2e}", flush=True)
        with open(out_mp3 + ".json", "w", encoding="utf-8") as f:
            json.dump({"rms": round(rms, 1), "smeter_avg": savg, "smeter_max": smax,
                       "n_audio": n_audio, "dauer_s": round(len(samples) / RATE, 1),
                       "nr": bool(nr_enabled), "nr_threshold": nr_threshold},
                      f)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
