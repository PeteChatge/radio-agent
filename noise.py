"""Rauschen-Scanner fuer Funkaufnahmen (MP3).

Findet Aufnahmen, die NUR Rauschen enthalten (keinen Sender) und stellt sie
mit Loeschfeld in der WebApp dar.

Kriterium (an weissem Rauschen -> flachheit ~0.56, an Sprache/Sender < 0.35
kalibriert):
  * spektrale Flachheit (Median ueber Frames) >= 0.40  -> breitbandig,
    kein tonales/formantisches Signal (kein Sender)
  * UND Spektralspitze (Median peak/mean) <= 40         -> kein Traeger/Whistle
Grenzfaelle mit Flachheit 0.35..0.40 werden als "borderline" markiert und
NICHT automatisch ausgewaehlt.

Benutzung:
  noise.py scan [verzeichnis ...]   # scannt (Default /archive) -> Report
  noise.py list                     # druckt Rauschdateien + Loeschbefehl
  noise.py delete <datei> [...]     # loescht MP3 (+ .json-Sidecar), nur /archive

Report: /archive/noise-report.json
"""
import concurrent.futures as cf
import argparse
import json
import os
import subprocess
import sys
import numpy as np
import rxdetect

ARCH = "/archive"
REPORT = os.path.join(ARCH, "noise-report.json")
SR = 8000
FLAT_NOISE = rxdetect.FLAT_NOISE
FLAT_CONTENT = rxdetect.FLAT_CONTENT
PEAK_CONTENT = rxdetect.PEAK_CONTENT


def decode(path):
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
         "-f", "s16le", "-"],
        capture_output=True)
    if p.returncode != 0 or not p.stdout:
        return None
    return np.frombuffer(p.stdout, dtype="<i2").astype(np.float64) / 32768.0


def features(x):
    if x is None:
        return None
    return rxdetect.analyze(x, SR, frame_s=0.5, nfft=1024)


def verdict(f):
    return rxdetect.verdict(f)


def sender_of(path):
    rel = os.path.relpath(path, ARCH).split(os.sep)
    return rel[1] if len(rel) >= 2 else "?"


def analyze(path):
    f = features(decode(path))
    try:
        st = os.stat(path)
        size, mtime = st.st_size, int(st.st_mtime)
    except OSError:
        size, mtime = 0, 0
    return {"path": path, "sender": sender_of(path), "size": size,
            "mtime": mtime, "verdict": verdict(f), **(f or {})}


def find_mp3(dirs):
    out = []
    for d in dirs:
        for root, _dirs, files in os.walk(d):
            for name in files:
                if name.lower().endswith(".mp3"):
                    out.append(os.path.join(root, name))
    return sorted(out)


LOCK = "/tmp/noise-scan.lock"


def scan(dirs):
    with open(LOCK, "w") as fh:
        fh.write(str(os.getpid()))
    try:
        paths = find_mp3(dirs)
        results = []
        with cf.ProcessPoolExecutor(max_workers=4) as ex:
            for i, r in enumerate(ex.map(analyze, paths), 1):
                results.append(r)
                if i % 25 == 0:
                    print(f"  {i}/{len(paths)}", flush=True)
        results.sort(key=lambda r: r["path"])
        report = {
            "generated_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc).isoformat(),
            "scanned": paths, "count": len(results),
            "noise": [r for r in results if r["verdict"] == "noise"],
            "borderline": [r for r in results if r["verdict"] == "borderline"],
            "audio": [r for r in results if r["verdict"] == "audio"],
        }
        os.makedirs(ARCH, exist_ok=True)
        tmp = REPORT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, REPORT)
        print(f"REPORT {REPORT}: noise={len(report['noise'])} "
              f"borderline={len(report['borderline'])} audio={len(report['audio'])}",
              flush=True)
        return report
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


def move_entries(entries, dest, keep_senders=()):
    """Verschiebt MP3 (+ Sidecar) nach dest. Rueckgabe: (done, skipped, failed).

    keep_senders: Sender-Namen, die NICHT verschoben werden (z.B.
    "Radio-Romania-International").
    """
    import shutil
    dest = os.path.realpath(dest)
    os.makedirs(dest, exist_ok=True)
    done, skipped, failed = [], [], []
    for r in entries:
        src = r["path"]
        if r.get("sender") in keep_senders:
            skipped.append(src)
            continue
        if not os.path.exists(src):
            failed.append(src)
            continue
        base = os.path.basename(src)
        target = os.path.join(dest, base)
        k = 1
        while os.path.exists(target) or os.path.exists(target + ".json"):
            target = os.path.join(dest, f"{os.path.splitext(base)[0]}-{k}.mp3")
            k += 1
        try:
            shutil.move(src, target)
        except OSError:
            failed.append(src)
            continue
        if os.path.exists(src + ".json"):
            try:
                shutil.move(src + ".json", target + ".json")
            except OSError:
                pass
        done.append(src)
    # Report aktualisieren
    rep = load_report()
    if rep:
        gone = set(done)
        for key in ("noise", "borderline", "audio"):
            rep[key] = [r for r in rep.get(key, []) if r["path"] not in gone]
        rep.setdefault("moved", []).extend(
            {"src": r["path"], "dst": os.path.join(dest, os.path.basename(r["path"]))}
            for r in entries if r["path"] in gone)
        tmp = REPORT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, REPORT)
    return done, skipped, failed


def load_report():
    try:
        with open(REPORT, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def delete(paths):
    """Loescht MP3 + Sidecar, ausschliesslich unter erlaubten Wurzeln."""
    roots = [os.path.realpath(ARCH), os.path.realpath("/tmp/rec-test")]
    done, failed = [], []
    for p in paths:
        rp = os.path.realpath(p)
        if not any(rp.startswith(r + os.sep) for r in roots) or not rp.lower().endswith(".mp3"):
            failed.append(p)
            continue
        ok = True
        for target in (rp, rp + ".json"):
            try:
                os.remove(target)
            except FileNotFoundError:
                pass
            except OSError:
                if target == rp:
                    ok = False
        (done if ok else failed).append(p)
    # geloeschte Eintraege aus dem Report entfernen
    rep = load_report()
    if rep:
        gone = set(done)
        for key in ("noise", "borderline", "audio"):
            rep[key] = [r for r in rep.get(key, []) if r["path"] not in gone]
        tmp = REPORT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, REPORT)
    return done, failed


def main():
    ap = argparse.ArgumentParser(description="Rauschen-Scanner fuer Funkaufnahmen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("dirs", nargs="*", default=[ARCH])
    sub.add_parser("list")
    m = sub.add_parser("move")
    m.add_argument("dest")
    m.add_argument("--keep-sender", action="append", default=[])
    m.add_argument("--include-borderline", action="store_true")
    d = sub.add_parser("delete")
    d.add_argument("files", nargs="+")
    d.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "scan":
        scan(args.dirs or [ARCH])
    elif args.cmd == "list":
        rep = load_report() or {}
        for r in rep.get("noise", []):
            print(f"rm -f '{r['path']}' '{r['path']}.json'   "
                  f"# flat={r['flatness']} lra={r['lra_db']} peak={r['peakbin']}")
        print(f"# {len(rep.get('noise', []))} Rauschdateien")
    elif args.cmd == "move":
        rep = load_report() or {}
        entries = list(rep.get("noise", []))
        if args.include_borderline:
            entries += rep.get("borderline", [])
        done, skipped, failed = move_entries(entries, args.dest,
                                             keep_senders=set(args.keep_sender))
        print(json.dumps({"moved": len(done), "skipped": len(skipped),
                          "failed": len(failed)}, ensure_ascii=False))
        for p in failed:
            print("  FEHLER", p, file=sys.stderr)
        sys.exit(1 if failed else 0)
    elif args.cmd == "delete":
        done, failed = delete(args.files)
        if args.json:
            print(json.dumps({"deleted": done, "failed": failed}))
        else:
            print(f"geloescht: {len(done)}  fehlgeschlagen: {len(failed)}")
            for p in failed:
                print("  FEHLER", p, file=sys.stderr)
        sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
