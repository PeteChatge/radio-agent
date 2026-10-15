"""DRM-Schedule automatisch online holen -> /archive/schedule/drm.json.

Quelle: offizieller DRM-Konsortium-Sendeplan
        https://www.drm.org/drm-in-the-world/broadcast-schedule/
Die Frequenzen aendern sich unregelmäßig (Saisonwechsel A26/B26, Tests),
darum wird der Plan periodisch (taeglich) neu eingelesen.

Erzeugt Eintraege im Format der uebrigen Schedules:
  {"sender","freqHz","mode":"drm","startUTC","endeUTC","tage":[..],
   "sprache","profil","gesperrt":false,"source":"drm.org"}
und schreibt sie nach /archive/schedule/drm.json.

Aufruf:  drm_fetch.py [--url URL] [--out DATEI] [--include-mw-india]
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request

from record import profile_cfg

URL = "https://www.drm.org/drm-in-the-world/broadcast-schedule/"
OUT = "/archive/schedule/drm.json"
UA = "Mozilla/5.0 (radio-agent DRM schedule fetcher)"

SW_PROFILES = ["scan2", "scan3", "scan4", "scan5", "scan6",
               "scan7", "scan8", "scan9", "scan10"]
DAY_SEED = {"mon": 2, "tue": 3, "wed": 4, "thu": 5, "fri": 6, "sat": 7, "sun": 1}
ALL_SEEDS = [1, 2, 3, 4, 5, 6, 7]


def fetch(url=URL, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return raw.decode("utf-8", "replace")


def _cells(row_html):
    cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S)
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip() for c in cells]


def _rows(table_html):
    return [_cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S)]


def parse_days(text):
    t = (text or "").lower()
    if "daily" in t or "täglich" in t or "taeglich" in t:
        return ALL_SEEDS
    seeds = set()
    for m in re.finditer(r"(mon|tue|wed|thu|fri|sat|sun)", t):
        seeds.add(DAY_SEED[m.group(1)])
    # Bereiche wie "sun-fri"
    for m in re.finditer(r"(mon|tue|wed|thu|fri|sat|sun)\s*-\s*(mon|tue|wed|thu|fri|sat|sun)", t):
        order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        a, b = order.index(m.group(1)), order.index(m.group(2))
        rng = order[a:b + 1] if a <= b else order[a:] + order[:b + 1]
        for d in rng:
            seeds.add(DAY_SEED[d])
    return sorted(seeds) if seeds else []


def parse_times(text):
    out = []
    for m in re.finditer(r"(\d{3,4})\s*-\s*(\d{3,4})", text or ""):
        s, e = m.group(1).zfill(4), m.group(2).zfill(4)
        out.append((f"{s[:2]}:{s[2:]}", f"{e[:2]}:{e[2:]}"))
    return out


def parse_freqs(text):
    out = []
    for m in re.finditer(r"\b(\d{3,5})\b", text or ""):
        khz = int(m.group(1))
        if 100 <= khz <= 30000:
            out.append(khz * 1000)
    return out


def profile_for(freq_hz):
    if 531000 <= freq_hz <= 1602000:
        return "sdrplay|scanmw"
    for p in SW_PROFILES:
        center, samp = profile_cfg(f"sdrplay|{p}")
        if abs(freq_hz - center) <= samp // 2 - 50000:
            return f"sdrplay|{p}"
    return None


def _slug(s):
    keep = []
    for c in (s or "DRM"):
        if c.isalnum() or c in "-_":
            keep.append(c)
        elif not keep or keep[-1] != "-":
            keep.append("-")
    return "".join(keep).strip("-") or "DRM"


def build_entries(html, include_mw_india=False):
    tables = re.findall(r"<table.*?</table>", html, re.S)
    entries, seen = [], set()

    def add(sender, freq_hz, times, days, sprache, site, source):
        profil = profile_for(freq_hz)
        if not profil:
            return
        for start, ende in times:
            key = (sender, freq_hz, start, ende)
            if key in seen:
                continue
            seen.add(key)
            entries.append({
                "sender": sender, "freqHz": freq_hz, "mode": "drm",
                "startUTC": start, "endeUTC": ende, "tage": days,
                "sprache": sprache or "", "site": site or "",
                "profil": profil, "prio": 5, "gesperrt": False, "source": source,
            })

    # Tabelle 0: HF-Sendeplan (Time|Broadcaster|Freq|Days|Language|Target|Site|Power|Beam)
    if tables:
        for row in _rows(tables[0]):
            if len(row) < 5 or row[0].lower().startswith("time in"):
                continue
            sender, freq_txt, days_txt = row[1], row[2], row[3]
            sprache = row[4] if len(row) > 4 else ""
            site = row[6] if len(row) > 6 else ""
            freqs = parse_freqs(freq_txt)
            times = parse_times(row[0]) or parse_times(freq_txt)
            if not freqs or not times:
                continue
            days = parse_days(days_txt)
            for f in freqs:
                add(_slug(sender), f, times, days, sprache, site, "drm.org")

    # Tabelle 2: Indien MW-DRM (Location|Frequency|Power|Mode|Content|Note)
    if include_mw_india and len(tables) > 2:
        for row in _rows(tables[2]):
            if len(row) < 4 or row[0].lower() == "location":
                continue
            loc, freq_txt, _power, mode, content = row[0], row[1], row[2], row[3], \
                (row[4] if len(row) > 4 else "")
            freqs = parse_freqs(freq_txt)
            times = parse_times(mode)
            days = parse_days(mode)
            for f in freqs:
                add(_slug(f"DRM-India-{loc.split(',')[0]}"), f, times,
                    days or ALL_SEEDS, content, loc, "drm.org-india")
    return entries


def fetch_and_write(url=URL, out=OUT, include_mw_india=False):
    html = fetch(url)
    entries = build_entries(html, include_mw_india)
    doc = {
        "source": url,
        "fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "count": len(entries),
        "entries": entries,
    }
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, out)
    return doc


def main():
    ap = argparse.ArgumentParser(description="DRM-Schedule online holen")
    ap.add_argument("--url", default=URL)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--include-mw-india", action="store_true")
    args = ap.parse_args()
    try:
        doc = fetch_and_write(args.url, args.out, args.include_mw_india)
    except Exception as e:  # noqa: BLE001
        print(f"DRM-FETCH-FEHLER: {e}", file=sys.stderr)
        return 1
    print(f"DRM {doc['count']} Eintraege -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
