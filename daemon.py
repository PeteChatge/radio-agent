"""Radio-Agent Recorder-Daemon (Ticket 05): 24/7, Schleife alle 30 s.

- Sprache (am/usb/...) aus /archive/schedule/deutsch.json: hoechste Prio gewinnt,
  10-min-Chunks via record.py, now.json fuer WebApp Kasten 1.
- Qualitaets-Gate: Chunk mit mean_volume unter SCHWELLE_DB wird automatisch
  geloescht (Rauschen/Leerlauf), geloggt.
- Fax: OWRX Background-Decoder parkt per Scheduler (fax49 Tag/fax80 Nacht),
  Bilder aus Container-/tmp/openwebrx -> /archive/fax/inbox/.
- Retention: Dateien aelter 60 Tage loeschen (1x/Tag).
Stop per SIGTERM oder systemctl stop (Aus-Schalter in WebApp).
"""
import datetime as dt
import json
import os
import signal
import subprocess
import time

BASE = "/opt/radio-agent"
ARCH = "/archive"
SCHED = os.path.join(ARCH, "schedule", "deutsch.json")
NOW = os.path.join(ARCH, "now.json")
CHUNK = 600
TICK = 30
RETENTION_DAYS = 60
def loudness_range(path):
    """Loudness Range (LRA) via ebur128, None bei Fehler. Sprache/Musik: hoch,
    Dauerrauschen/Dauertraeger: niedrig."""
    import re
    try:
        r = subprocess.run(["ffmpeg", "-v", "info", "-i", path, "-af", "ebur128=peak=true",
                            "-f", "null", "-"], capture_output=True, text=True, timeout=180)
        m = re.search(r"LRA:\s*(-?[0-9.]+)", r.stderr)
        return float(m.group(1)) if m else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


SCHWELLE_DB = -35.0     # darunter = Stille -> Quarantaene
SCHWELLE_LRA = 4.0      # darunter = flach wie Rauschen -> Quarantaene
SCHWELLE_SMETER = 1e-7  # darunter = kein Signal an der Antenne -> Quarantaene
AUDIO_MODES = ("am", "usb", "lsb", "nfm", "wfm")
PY = os.path.join(BASE, "venv", "bin", "python")
REC = os.path.join(BASE, "record.py")
FAX_TMP = "/tmp/openwebrx"

stop = False
current_proc = None


def log(*a):
    print(dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S"), *a, flush=True)


def on_term(signum, frame):
    global stop
    stop = True
    if current_proc is not None:
        try:
            current_proc.terminate()
        except OSError:
            pass


def load_sched():
    with open(SCHED, encoding="utf-8") as f:
        return json.load(f).get("entries", [])


def active_job(entries, now):
    py2seed = {0: 2, 1: 3, 2: 4, 3: 5, 4: 6, 5: 7, 6: 1}
    best = None
    for e in entries:
        if e.get("mode") not in AUDIO_MODES or not e.get("startUTC") or not e.get("endeUTC"):
            continue
        try:
            sh, sm = (int(x) for x in e["startUTC"].split(":")[:2])
            eh, em = (int(x) for x in e["endeUTC"].split(":")[:2])
        except ValueError:
            continue
        if py2seed[now.weekday()] not in (e.get("tage") or []):
            continue
        t = now.time()
        s, en = dt.time(sh, sm), dt.time(eh, em)
        inside = s <= t < en if s < en else (s <= t or t < en)
        if inside and (best is None or (e.get("prio", 0) > best.get("prio", 0))):
            best = e
    return best


def slug(name):
    keep = []
    for c in (name or "unbekannt"):
        if c.isalnum() or c in "-_":
            keep.append(c)
        elif not keep or keep[-1] != "-":
            keep.append("-")
    return "".join(keep).strip("-") or "sender"


def write_now(job):
    if job is None:
        try:
            os.remove(NOW)
        except OSError:
            pass
        return
    tmp = NOW + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"sender": job.get("sender"), "freqHz": job.get("freqHz"),
                   "mode": job.get("mode"), "sprache": job.get("sprache"),
                   "since": dt.datetime.now(dt.timezone.utc).strftime("%H:%M"),
                   "profil": job.get("profil")}, f, ensure_ascii=False)
    os.replace(tmp, NOW)


def mean_db(path):
    """Mittlerer Pegel via ffmpeg volumedetect, None bei Fehler."""
    try:
        r = subprocess.run(["ffmpeg", "-v", "info", "-i", path, "-af", "volumedetect",
                            "-f", "null", "-"], capture_output=True, text=True, timeout=120)
        for line in r.stderr.splitlines():
            if "mean_volume" in line:
                return float(line.split("mean_volume:")[1].split("dB")[0].strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def record_chunk(job):
    global current_proc
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M")
    sdir = os.path.join(ARCH, "audio", slug(job.get("sender")))
    os.makedirs(sdir, exist_ok=True)
    base = f"{ts}_{job['freqHz'] // 1000}kHz_{job['mode']}_{slug(job.get('sender'))}"
    out = os.path.join(sdir, base + ".mp3")
    wav = out + ".wav"
    log("REC-START", base)
    write_now(job)
    with open(os.path.join(BASE, "last-rec.log"), "wb") as lf:
        current_proc = subprocess.Popen(
            [PY, REC, job["profil"], str(job["freqHz"]), job["mode"], str(CHUNK), out],
            stdout=lf, stderr=subprocess.STDOUT)
    t0 = time.monotonic()
    while current_proc.poll() is None and not stop:
        time.sleep(2)
    if stop and current_proc.poll() is None:
        current_proc.terminate()
        try:
            current_proc.wait(timeout=15)
        except subprocess.SubprocessError:
            current_proc.kill()
    rc = current_proc.poll()
    current_proc = None
    if stop:
        write_now(None)
        return
    if rc != 0 or not os.path.exists(out):
        log("REC-FEHLER rc=", rc, base, "(siehe last-rec.log, 60s Pause)")
        time.sleep(60)
        return
    pegel = mean_db(wav if os.path.exists(wav) else out)
    lra = loudness_range(wav if os.path.exists(wav) else out)
    try:
        with open(out + ".json", encoding="utf-8") as f:
            rec = json.load(f)
    except OSError:
        rec = {}
    smax = rec.get("smeter_max", 0) or 0
    try:
        if os.path.exists(wav):
            os.remove(wav)
    except OSError:
        pass
    grund = None
    if pegel is not None and pegel < SCHWELLE_DB:
        grund = f"stille {pegel:.1f}dB"
    elif lra is not None and lra < SCHWELLE_LRA:
        grund = f"flach LRA {lra:.1f}LU"
    elif smax < SCHWELLE_SMETER:
        grund = f"kein Signal S={smax:.1e}"
    if grund is not None:
        # Quarantaene statt Loeschen: Stichprobe bleibt hoerbar
        qdir = os.path.join(ARCH, "quarantaene", slug(job.get("sender")))
        os.makedirs(qdir, exist_ok=True)
        for p in (out, out + ".json"):
            try:
                os.rename(p, os.path.join(qdir, os.path.basename(p)))
            except OSError:
                pass
        log(f"QUARANTAENE ({grund})", base)
        return
    with open(out + ".json", "w", encoding="utf-8") as f:
        json.dump({"sender": job.get("sender"), "freqHz": job.get("freqHz"),
                   "mode": job.get("mode"), "sprache": job.get("sprache"),
                   "profil": job.get("profil"), "chunk_s": CHUNK,
                   "mean_db": pegel, "lra": lra, "smeter_max": smax,
                   "recorded_utc": dt.datetime.now(dt.timezone.utc).isoformat()},
                  f, ensure_ascii=False, indent=1)
    log(f"REC-OK pegel={pegel if pegel is None else round(pegel, 1)}dB", base)


def collect_fax(seen):
    """Neue Decoder-Bilder aus Container-tmp -> /archive/fax/inbox/."""
    try:
        r = subprocess.run(["docker", "exec", "owrx-mbe", "ls", "-1", FAX_TMP],
                           capture_output=True, timeout=30)
    except OSError:
        return
    if r.returncode != 0:
        return
    # Dateinamen koennen Latin-1-Bytes enthalten (° etc.) -> surrogateescape
    names = r.stdout.decode("utf-8", "surrogateescape").split()
    dest = os.path.join(ARCH, "fax", "inbox")
    os.makedirs(dest, exist_ok=True)
    for name in names:
        if not name or name in seen:
            continue
        if not name.lower().endswith((".png", ".bmp", ".jpg", ".txt")):
            seen.add(name)
            continue
        tmp = os.path.join("/tmp", "fax-" + name)
        c = subprocess.run(["docker", "cp", f"owrx-mbe:{FAX_TMP}/{name}", tmp],
                           capture_output=True, timeout=60)
        if c.returncode == 0 and os.path.exists(tmp):
            ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M")
            os.rename(tmp, os.path.join(dest, f"{ts}_{name}"))
            log("FAX", name)
        seen.add(name)


def retention():
    cutoff = time.time() - RETENTION_DAYS * 86400
    n = 0
    for kind in ("audio", "fax", "quarantaene"):
        base = os.path.join(ARCH, kind)
        for root, _dirs, files in os.walk(base):
            for name in files:
                p = os.path.join(root, name)
                try:
                    if os.stat(p).st_mtime < cutoff:
                        os.remove(p)
                        n += 1
                except OSError:
                    pass
    if n:
        log(f"RETENTION {n} Dateien >{RETENTION_DAYS}d geloescht")


def main():
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    log(f"DAEMON-START chunk={CHUNK}s schwelle={SCHWELLE_DB}dB retention={RETENTION_DAYS}d")
    try:
        entries = load_sched()
    except OSError as e:
        log("SCHEDULE-FEHLER", e)
        return
    seen_fax = set()
    last_fax = 0.0
    last_ret = 0.0
    while not stop:
        now = dt.datetime.now(dt.timezone.utc)
        if time.time() - last_fax > 300:
            collect_fax(seen_fax)
            last_fax = time.time()
        if time.time() - last_ret > 86400:
            retention()
            last_ret = time.time()
        try:
            entries = load_sched()
        except OSError:
            pass
        job = active_job(entries, now)
        if job is None:
            write_now(None)
            time.sleep(TICK)
            continue
        record_chunk(job)
    write_now(None)
    log("DAEMON-STOPP")


if __name__ == "__main__":
    main()
