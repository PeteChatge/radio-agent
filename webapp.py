"""Radio-Agent WebApp: statische index.html + 3 JSON-APIs, Port 18088.

GET /            -> webapp-root/index.html
GET /api/now     -> {active, radio{name, location, clients}}
GET /api/files   -> [{kind, sender, name, size, mtime, url}] (audio + fax)
GET /api/jobs    -> naechste Aussendungen aus /archive/schedule/deutsch.json
GET /audio/...   -> mp3 aus /archive/audio
GET /fax/...     -> Bild aus /archive/fax
Nur Stdlib. Start per systemd (radio-webapp.service).
"""
import datetime as dt
import json
import mimetypes
import os
import subprocess
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = "/opt/radio-agent/webapp-root"
ARCH = "/archive"
PORT = 18088


def daemon_running():
    try:
        r = subprocess.run(["systemctl", "is-active", "radio-recorder"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "active"
    except OSError:
        return False


def scan(kind):
    out = []
    base = os.path.join(ARCH, {"audio": "audio", "fax": "fax"}.get(kind, "quarantaene"))
    if not os.path.isdir(base):
        return out
    for sender in sorted(os.listdir(base)):
        sdir = os.path.join(base, sender)
        if not os.path.isdir(sdir):
            continue
        for name in sorted(os.listdir(sdir)):
            if name.endswith(".json"):
                continue
            p = os.path.join(sdir, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append({"kind": kind, "sender": sender, "name": name,
                        "size": st.st_size, "mtime": int(st.st_mtime),
                        "url": f"/{kind}/{sender}/{name}"})
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


def next_jobs(limit=8):
    try:
        with open(os.path.join(ARCH, "schedule", "deutsch.json"), encoding="utf-8") as f:
            sched = json.load(f)
    except OSError:
        return []
    now = dt.datetime.now(dt.timezone.utc)
    # Tage-Konvention Seed: 1=So..7=Sa (short-wave.info); leere Liste = kein Termin
    py2seed = {0: 2, 1: 3, 2: 4, 3: 5, 4: 6, 5: 7, 6: 1}  # Monday=0 -> 2 ..
    jobs = []
    for e in sched.get("entries", []):
        if not e.get("startUTC") or not e.get("tage"):
            continue
        try:
            hh, mm = (int(x) for x in e["startUTC"].split(":")[:2])
        except ValueError:
            continue
        for ahead in range(0, 8):
            day = now + dt.timedelta(days=ahead)
            if py2seed[day.weekday()] in e["tage"]:
                at = day.replace(hour=hh, minute=mm, second=0, microsecond=0)
                if at > now:
                    jobs.append({"sender": e.get("sender"), "freqHz": e.get("freqHz"),
                                 "mode": e.get("mode"), "sprache": e.get("sprache"),
                                 "at": at.isoformat(), "in_min": int((at - now).total_seconds() // 60)})
                    break
    jobs.sort(key=lambda j: j["at"])
    return jobs[:limit]


def radio_state():
    try:
        with urllib.request.urlopen("http://localhost:8073/status.json", timeout=5) as r:
            d = json.load(r)
        return {"name": d["receiver"]["name"], "location": d["receiver"]["location"],
                "version": d.get("version")}
    except Exception:
        return {"name": "?", "location": "?", "version": "?"}


class Handler(BaseHTTPRequestHandler):
    server_version = "RadioAgent/1"

    def log_message(self, *a):
        pass

    def send_json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            return self.send_file(os.path.join(ROOT, "index.html"))
        if self.path == "/api/now":
            try:
                with open(os.path.join(ARCH, "now.json"), encoding="utf-8") as f:
                    active = json.load(f)
            except OSError:
                active = None
            return self.send_json({"active": active, "radio": radio_state()})
        if self.path == "/api/files":
            return self.send_json(scan("audio") + scan("fax") + scan("quarantaene"))
        if self.path == "/api/jobs":
            return self.send_json(next_jobs())
        if self.path == "/api/daemon":
            return self.send_json({"running": daemon_running()})
        if self.path.startswith(("/audio/", "/fax/", "/quarantaene/")):
            kind, rest = self.path[1:].split("/", 1)
            base = os.path.join(ARCH, {"audio": "audio", "fax": "fax"}.get(kind, "quarantaene"))
            p = os.path.realpath(os.path.join(base, rest))
            if not p.startswith(os.path.realpath(base) + os.sep):
                return self.send_error(403)
            return self.send_file(p)
        return self.send_error(404)

    def do_POST(self):
        if self.path == "/api/daemon":
            try:
                n = int(self.headers.get("Content-Length", 0))
                action = json.loads(self.rfile.read(n) or b"{}").get("action")
            except (ValueError, OSError):
                return self.send_error(400)
            if action not in ("start", "stop"):
                return self.send_error(400)
            r = subprocess.run(["sudo", "systemctl", action, "radio-recorder"],
                               capture_output=True, timeout=30)
            time.sleep(2)
            return self.send_json({"running": daemon_running(), "rc": r.returncode})
        return self.send_error(404)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
