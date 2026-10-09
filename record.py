"""OWRX headless recorder v1 (Ticket 01).

Tunt per WS (selectprofile + dspcontrol), sammelt 0x02-Frames (IMA-ADPCM),
schreibt WAV (12 kHz mono) + mp3 (64k).

Aufruf: record.py <profile-id> <freqHz> <mod> <sekunden> <out.mp3>
Bsp:     record.py rtlsdr|am 792000 am 60 boehmen.mp3
"""
import asyncio
import json
import struct
import subprocess
import sys
import time
import wave

import websockets

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


async def main():
    profile, freq, mod, secs, out_mp3 = sys.argv[1], int(sys.argv[2]), sys.argv[3], float(sys.argv[4]), sys.argv[5]
    async with websockets.connect(URL, max_size=None, ping_interval=None) as ws:
        await ws.send("SERVER DE CLIENT client=openwebrx.js type=receiver")
        # wie der Browser: Audio-Raten melden (sonst kein Audio-Stream)
        await ws.send(json.dumps({"type": "connectionproperties",
                                  "params": {"output_rate": 12000, "hd_output_rate": 48000}}))
        # 1. Burst lesen: aktuellen Stand + Katalog merken
        want_sdr, want_profile = profile.split("|")
        got_sdr = got_profile = center = samp = None
        async with asyncio.timeout(15):
            async for msg in ws:
                if isinstance(msg, str):
                    try:
                        d = json.loads(msg)
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") == "profiles":
                        break
                    if d.get("type") == "config":
                        v = d.get("value", {})
                        for k in ("sdr_id", "profile_id", "center_freq", "samp_rate"):
                            if k in v:
                                if k == "sdr_id":
                                    got_sdr = v[k]
                                elif k == "profile_id":
                                    got_profile = v[k]
                                elif k == "center_freq":
                                    center = v[k]
                                else:
                                    samp = v[k]
        # 2. Nur bei Bedarf umschalten (Re-Select sendet keine Config)
        if got_sdr != want_sdr or got_profile != want_profile:
            await asyncio.sleep(3)
            await ws.send(json.dumps({"type": "selectprofile", "params": {"profile": profile}}))
            center = samp = None
            async with asyncio.timeout(20):
                async for msg in ws:
                    if isinstance(msg, str):
                        try:
                            d = json.loads(msg)
                        except json.JSONDecodeError:
                            continue
                        if d.get("type") != "config":
                            continue
                        v = d.get("value", {})
                        if "sdr_id" in v:
                            got_sdr = v["sdr_id"]
                        if "profile_id" in v:
                            got_profile = v["profile_id"]
                        if "center_freq" in v:
                            center = v["center_freq"]
                        if "samp_rate" in v:
                            samp = v["samp_rate"]
                        if got_sdr == want_sdr and got_profile == want_profile and center and samp:
                            break
        print(f"PROFILE sdr={got_sdr} profile={got_profile} center={center} samp={samp}", flush=True)
        assert center and samp, "keine Center-Config vom Server erhalten"
        offset = freq - center
        assert abs(offset) <= samp / 2, f"Freq {freq} ausserhalb Profil (offset {offset}, bw {samp})"
        # 3. Demodulator-Params + Start (wie Demodulator.js set/start).
        # Squelch weit offen (-150): Gating macht später der Recorder, nicht das DSP.
        # Kurz warten: Kette muss nach Profilwechsel erst stehen.
        await asyncio.sleep(3)
        await ws.send(json.dumps({"type": "dspcontrol", "params": {
            "low_cut": -4000, "high_cut": 4000, "offset_freq": offset,
            "mod": mod, "squelch_level": -150, "audio_service_id": -1,
            "dmr_filter": 0, "secondary_mod": "off", "secondary_offset_freq": 0}}))
        await ws.send(json.dumps({"type": "dspcontrol", "action": "start"}))
        # 4. 0x02-Frames sammeln
        dec = ImaDecoder()
        samples: list = []
        n_audio = n_other = 0
        t_end = time.monotonic() + secs
        async with asyncio.timeout(secs + 20):
            async for msg in ws:
                if time.monotonic() >= t_end:
                    break
                if isinstance(msg, bytes) and len(msg) > 1 and msg[0] == 0x02:
                    n_audio += 1
                    dec.feed(msg[1:], samples)
                else:
                    n_other += 1
        print(f"FRAMES audio={n_audio} andere={n_other} samples={len(samples)}", flush=True)
        if not samples:
            print("KEIN-AUDIO – Abbruch", flush=True)
            return
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
        print(f"FERTIG {out_mp3}", flush=True)


asyncio.run(main())
