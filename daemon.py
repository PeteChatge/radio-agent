"""Radio-Agent Recorder-Daemon (Ticket 05): 24/7, Schleife alle 30 s.

- Sprache (am/usb/...) aus /archive/schedule/deutsch.json.
- Parallel: mehrere Jobs im SELBEN (SDR,Profil) laufen gleichzeitig
  (eigener DSP pro Client, eigener Offset). Konflikt nur ueber Profile
  hinweg am selben Stick -> hoechste Prio gewinnt (Gleichstand: Seed-Reihenfolge).
- 10-min-Chunks via record.py (Kappung am Sendungsende), now.json, Sidecars.
- Qualitaets-Gate: Stille/LRA/S-Meter -> Quarantaene (Review statt Loeschen).
- Fax: OWRX Background-Decoder (Scheduler fax49/fax80), Bilder -> /archive/fax/inbox/.
- Retention 60 Tage. Stopp per SIGTERM / systemctl (WebApp-Schalter).
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
SCHWELLE_DB = -35.0
SCHWELLE_LRA = 4.0
SCHWELLE_SMETER = 1e-7
AUDIO_MODES = ("am", "usb", "lsb", "nfm", "wfm")
PY = os.path.join(BASE, "venv", "bin", "python")
REC = os.path.join(BASE, "record.py")
FAX_TMP = "/tmp/openwebrx"

stop = False
ongoing = {}  # jobkey -> {proc, job, out}


def log(*a):
    print(dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S"), *a, flush=True)


def on_term(signum, frame):
    global stop
    stop = True
    for e in ongoing.values():
        try:
            e["proc"].terminate()
        except OSError:
            pass


def load_sched():
    with open(SCHED, encoding="utf-8") as f:
        return json.load(f).get("entries", [])


def in_window(e, now):
    try:
        sh, sm = (int(x) for x in e["startUTC"].split(":")[:2])
        eh, em = (int(x) for x in e["endeUTC"].split(":")[:2])
    except (ValueError, KeyError, AttributeError):
        return False
    py2seed = {0: 2, 1: 3, 2: 4, 3: 5, 4: 6, 5: 7, 6: 1}
    if py2seed[now.weekday()] not in (e.get("tage") or []):
        return False
    t = now.time()
    s, en = dt.time(sh, sm), dt.time(eh, em)
    return (s <= t < en) if s < en else (s <= t or t < en)


def desired_jobs(entries, now):
    """Aktive Audio-Jobs nach Konflikt-Logik: pro Stick ein Profil (Top-Prio)."""
    cand = [e for e in entries
            if not e.get("gesperrt") and e.get("aktiv") is not False and e.get("mode") in AUDIO_MODES
            and e.get("startUTC") and e.get("endeUTC") and in_window(e, now)
            and e.get("profil")]
    by_sdr = {}
    for e in cand:
        sdr = e["profil"].split("|")[0]
        by_sdr.setdefault(sdr, []).append(e)
    out = []
    for sdr, jobs in by_sdr.items():
        by_prof = {}
        for e in jobs:
            by_prof.setdefault(e["profil"], []).append(e)
        top = max(by_prof.values(), key=lambda g: max(x.get("prio", 0) for x in g))
        out.extend(top)
    return out


def jobkey(job):
    return f"{job.get('sender')}|{job.get('freqHz')}"


def slug(name):
    keep = []
    for c in (name or "unbekannt"):
        if c.isalnum() or c in "-_":
            keep.append(c)
        elif not keep or keep[-1] != "-":
            keep.append("-")
    return "".join(keep).strip("-") or "sender"


def job_end(job, now):
    try:
        eh, em = (int(x) for x in job["endeUTC"].split(":")[:2])
    except (ValueError, KeyError):
        return None
    end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    if end <= now:
        end += dt.timedelta(days=1)
    return end


def write_now(jobs):
    if not jobs:
        try:
            os.remove(NOW)
        except OSError:
            pass
        return
    top = max(jobs, key=lambda j: j.get("prio", 0))
    tmp = NOW + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"active": {"sender": top.get("sender"), "freqHz": top.get("freqHz"),
                              "mode": top.get("mode"), "sprache": top.get("sprache"),
                              "since": dt.datetime.now(dt.timezone.utc).strftime("%H:%M"),
                              "profil": top.get("profil")},
                   "alle": [{"sender": j.get("sender"), "freqHz": j.get("freqHz")} for j in jobs]},
                  f, ensure_ascii=False)
    os.replace(tmp, NOW)


def mean_db(path):
    try:
        r = subprocess.run(["ffmpeg", "-v", "info", "-i", path, "-af", "volumedetect",
                            "-f", "null", "-"], capture_output=True, text=True, timeout=120)
        for line in r.stderr.splitlines():
            if "mean_volume" in line:
                return float(line.split("mean_volume:")[1].split("dB")[0].strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def loudness_range(path):
    import re
    try:
        r = subprocess.run(["ffmpeg", "-v", "info", "-i", path, "-af", "ebur128=peak=true",
                            "-f", "null", "-"], capture_output=True, text=True, timeout=180)
        m = re.search(r"LRA:\s*(-?[0-9.]+)", r.stderr)
        return float(m.group(1)) if m else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def start_chunk(job):
    now = dt.datetime.now(dt.timezone.utc)
    end = job_end(job, now)
    secs = CHUNK
    if end is not None:
        secs = max(60, min(CHUNK, int((end - now).total_seconds())))
    ts = now.strftime("%Y%m%d_%H%M")
    sdir = os.path.join(ARCH, "audio", slug(job.get("sender")))
    os.makedirs(sdir, exist_ok=True)
    base = f"{ts}_{job['freqHz'] // 1000}kHz_{job['mode']}_{slug(job.get('sender'))}"
    out = os.path.join(sdir, base + ".mp3")
    log("REC-START", base)
    with open(os.path.join(BASE, "last-rec.log"), "wb") as lf:
        proc = subprocess.Popen(
            [PY, REC, job["profil"], str(job["freqHz"]), job["mode"], str(secs), out],
            stdout=lf, stderr=subprocess.STDOUT)
    ongoing[jobkey(job)] = {"proc": proc, "job": job, "out": out, "base": base}


def finish_chunk(key, entry):
    """Abgeschlossener Chunk: Gate -> Archiv oder Quarantaene."""
    job, out, base = entry["job"], entry["out"], entry["base"]
    wav = out + ".wav"
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
                   "profil": job.get("profil"), "mean_db": pegel, "lra": lra,
                   "smeter_max": smax,
                   "recorded_utc": dt.datetime.now(dt.timezone.utc).isoformat()},
                  f, ensure_ascii=False, indent=1)
    log(f"REC-OK pegel={pegel if pegel is None else round(pegel, 1)}dB", base)


def collect_fax(seen):
    try:
        r = subprocess.run(["docker", "exec", "owrx-mbe", "ls", "-1", FAX_TMP],
                           capture_output=True, timeout=30)
    except OSError:
        return
    if r.returncode != 0:
        return
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
    log("DAEMON-START parallel-chunks")
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
        want = {jobkey(j): j for j in desired_jobs(entries, now)}
        for key in [k for k in ongoing if k not in want]:
            try:
                ongoing[key]["proc"].terminate()
            except OSError:
                pass
        for key, entry in list(ongoing.items()):
            rc = entry["proc"].poll()
            if rc is None:
                continue
            del ongoing[key]
            if stop:
                continue
            if rc != 0 or not os.path.exists(entry["out"]):
                log("REC-FEHLER rc=", rc, entry["base"], "(60s Pause)")
                time.sleep(60)
                continue
            finish_chunk(key, entry)
        if not stop:
            for key, job in want.items():
                if key not in ongoing:
                    start_chunk(job)
        write_now([e["job"] for e in ongoing.values()] or
                  [want[k] for k in want] or [])
        time.sleep(TICK)
    write_now([])
    log("DAEMON-STOPP")


if __name__ == "__main__":
    main()
