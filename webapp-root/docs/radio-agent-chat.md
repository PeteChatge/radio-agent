# Radio-Agent — Chat-Protokoll / Arbeitsstand

> Aufzeichnung der opencode-Sitzung auf dem Raspberry Pi (host `raspi`, user `olaf`).
> Stand: 2026-10-14. Ablage des Downloads: `webapp-root/docs/` → URL `http://<pi>:18088/docs/radio-agent-chat.md`.

## Ziel

Radioaufnahme-Agent auf Raspberry Pi 4 ausbauen:

- **Ganzband-Scan** (ganzes Band durchstimmen) mit **Rausch-Gate**, sodass **nur echte Sender** aufgenommen werden.
- Bei Rauschen Job **ad hoc beenden** und **weiterscannen bis ein gutes Signal anliegt**.
- **DRM-Frequenzen automatisch online** holen.

## Umgebung

- Linux `raspi` 6.18.34 aarch64. `ffmpeg`/`ffprobe`/`python3` vorhanden.
- `scipy`/`audioop` fehlen; System-Python 3.14.6, `numpy` 2.5.4.
- venv `/opt/radio-agent/venv` (python3.13) mit `websockets`, `numpy`.
- Netz/Web verfügbar (curl MZ Mozilla ok).

### OpenWebRX+

- OpenWebRX+ v1.2.125 (`slechev/openwebrxplus-softmbe`), Docker-Container **`owrx-mbe`** (Port 8073, `ws://localhost:8073/ws/`).
- Mounts: `/opt/owrx-docker/etc → /etc/openwebrx`, `/opt/owrx-docker/var → /var/lib/openwebrx`, `/opt/owrx-docker/plugins → …/htdocs/plugins`.
- Aktive Config: `/opt/owrx-docker/var/settings.json`.
- WebApp `radio-webapp` auf `0.0.0.0:18088`.
- `allow_center_freq_changes = False` → für volle MW-Abdeckung **eigene Scan-Profile** nötig (nicht per Center-Retune).
- WS-Handshake nötig (`client=openwebrx.js type=receiver`); Audio = binäre `0x02`-Frames (IMA-ADPCM), dazu JSON-Statusmeldungen.
- DRM: `csdr.chain.drm.Drm` (Dream). `Bandpass(-5000, 5000)` → DSP-BW **10 kHz**, feste IF-/Audio-Rate **48000**, `squelch=False`. Ohne Lock liefert Dream **kein Audio**.

### Entscheidungen

- **Auto-Scan-Fallback**: Rausch-Job wird fürs ganze Fenster gesperrt; solange kein geplantes Signal gut ist, läuft Ganzband-Auto-Scan; sobald ein gutes Signal anliegt → geplanter Job.
- Signal-Schwelle = **Kalibrierung**: Flachheit `< 0.35` oder Peakbin `> 40` = Sender; `0.35–0.40` = Grenzfall; `≥ 0.40` = Rauschen. Weißes Rauschen ~0.56.
- DRM ohne Lock: **längerer Lock-Versuch 90 s** (user-Entscheid), danach 120 s Job-Pause/Retry.

## Änderungen / Dateien

- **`/opt/radio-agent/rxdetect.py`** (neu): gemeinsamer Rausch-/Signal-Detektor.
  `FLAT_NOISE=0.40`, `FLAT_CONTENT=0.35`, `PEAK_CONTENT=40.0`; `analyze(x, sr, frame_s=0.5, nfft=1024)`, `verdict()` → „audio/noise".
- **`/opt/radio-agent/noise.py`**: nutzt jetzt `rxdetect` (gleiches Verhalten validiert).
- **`/opt/radio-agent/record.py`** (v2):
  - Noise-Reduction standardmäßig **+5**; `NR_ENABLED`, `NR_THRESHOLD`; Noise-Gate über `PROBE_SECONDS`.
  - Rausch-Probe mit NR AUS; nur Rauschen → **Exit 3**, **keine Datei**.
  - DRM: **kein** Rausch-Gate (Dream erst nach Lock); Lock-Fenster **90 s** (`DRM_LOCK_SECONDS`), sonst `rc=1` (kein Audio).
  - DRM-Filter ±5000 Hz, sonst ±4000 Hz. Profil-Tabelle inkl. `sdrplay|scanmw` (Center 1066,5 kHz, 2 MS/s).
- **`/opt/radio-agent/scan.py`** (neu): Ganzband-Scanner.
  - `mw`: Profil `sdrplay|scanmw`, 531–1602 kHz / 9 kHz → 120 Kanäle.
  - `sw`: `sdrplay|scan2..scan10`, 1.5–20.4 MHz / 10 kHz → 1719 Kanäle.
  - Dwell 0.25 s; Rausch-Gate (NR aus); belegte Kanäle mit NR +5 → `/archive/scan/<band>/…`; reine Rauschkanäle keine Datei.
- **`/opt/radio-agent/drm_fetch.py`** (neu): holt Sendeplan von drm.org → `/archive/schedule/drm.json` (40 Einträge, inkl. RRI). Auto-Fetch im Daemon (`DRM_TTL`).
- **`/opt/radio-agent/noise.py`**, **`daemon.py`**: siehe unten.
- **`/opt/owrx-docker/var/settings.json`**: Profil `sdrplay|scanmw` (Center 1066,5 kHz, 2 MS/s, `start_mod am`, `tuning_step 9000`; „Scan MW 0.53–1.60“). Backup `settings.json.bak-20261014-scanmw`. Container `owrx-mbe` neu gestartet.
- **`/etc/systemd/system/radio-recorder.service`**, `radio-webapp.service`.

## Daemon-Änderungen (`/opt/radio-agent/daemon.py`)

Konstanten u. a.:

```python
SCAN_REC_SECONDS = 60
SCAN_MAX = 120
SCAN_COOLDOWN = 1800        # Pause zwischen explizit geplanten Scans
AUTOSCAN_COOLDOWN = 15      # Pause zwischen Auto-Fallback-Scans
DRM_TTL = 86400
NO_AUDIO_COOLDOWN = 120     # kein Audio (z.B. DRM ohne Lock): pro Job
ERROR_COOLDOWN = 120        # sonstiger Fehler: pro Job
```

- `drm`-Modus ergänzt; DRM-Auto-Fetch.
- Scan-Jobs (`"scan": "mw"/"sw"/"all"`).
- Fenster-Sperre für Rausch-Jobs (`suppressed`/`window_end_epoch`).
- Auto-Scan-Fallback in `compute_want`.

### Bug (behoben)

`want = compute_want(...)` wurde **vor** der Auswertung terminierter Jobs (und dem Setzen der Fenster-Sperre) berechnet → ein als Rauschen gesperrter Job startete sofort neu.

**Fix** — Reihenfolge im `main()`-Loop:

1. fertige Jobs verarbeiten (setzt ggf. `suppressed`),
2. `want = compute_want(...)`, überzählige laufende Jobs beenden,
3. fehlende Jobs starten.

Zusätzlich: globales `time.sleep(60)` bei Fehlern ersetzt durch **pro-Job `retry_after`-Cooldown**, damit ein fehlschlagender Job nicht den gesamten Daemon blockiert.

```python
if rc == 1:
    retry_after[key] = time.time() + NO_AUDIO_COOLDOWN
    log("KEIN-AUDIO rc=", rc, entry["base"], f"({NO_AUDIO_COOLDOWN}s Pause)")
    continue
if rc != 0 or not os.path.exists(entry["out"]):
    retry_after[key] = time.time() + ERROR_COOLDOWN
    log("REC-FEHLER rc=", rc, entry["base"], f"({ERROR_COOLDOWN}s Pause)")
    continue
```

Im Start-Block: `if time.time() < retry_after.get(key, 0): continue`.

### record.py — DRM-Zweig

```python
if mod == "drm":
    await ws.send(... dsp_params(profile, freq, mod, nr_enabled, nr_threshold))
    await ws.send(json.dumps({"type": "dspcontrol", "action": "start"}))
    drm_deadline = max(deadline, time.monotonic() + DRM_LOCK_SECONDS)
    samples, smeter, n_audio, n_other, aborted = await capture(
        ws, drm_deadline, abort_after_no_audio=DRM_LOCK_SECONDS)
    ...
    if aborted:
        print(f"DRM-KEIN-LOCK-{DRM_LOCK_SECONDS}s – Abbruch")
        return 1
else:
    # Probelauf mit NR AUS -> Rausch-Gate -> ggf. EXIT_NOISE
    # dann Restaufnahme mit NR AN
```

`DRM_LOCK_SECONDS = int(os.environ.get("DRM_LOCK_SECONDS", "90"))`

## Verifikation

- Analog Erfolgspfad: 1008 kHz → `PROBE audio` → mp3 geschrieben (`FERTIG`).
- Analog Rausch-Gate: 1170 kHz → `PROBE noise` → `RAUSCHEN – Aufnahme verworfen (keine Datei)`.
- DRM-Pfad (Override `DRM_LOCK_SECONDS=8`): `DRM-KEIN-LOCK-8s – Abbruch`, keine Datei.
- Scanner MW live: aktive Kanäle erkannt (z. B. 1008 kHz `flat=0.0874`), Rauschkanäle verworfen.
- Daemon live (neue PID):

```
16:52:41 RAUSCHEN verworfen – Job fuers Fenster gesperrt 5920kHz_am_HCJB-Deutsch-49m
16:52:41 REC-START 13690kHz_drm_Radio-Romania-International
16:53:41 KEIN-AUDIO rc=1 13690kHz_drm_RRI (120s Pause)    # kein globaler Stillstand mehr
```

## Betrieb

```bash
# Daemon/WebApp
sudo systemctl restart radio-recorder
sudo systemctl restart radio-webapp
systemctl is-active radio-recorder radio-webapp

# Log
journalctl -u radio-recorder --since "5 min ago" --no-pager | \
  grep -E "REC-START|RAUSCHEN|KEIN-AUDIO|AUTOSCAN|FERTIG"

# Manuell
cd /opt/radio-agent
PROBE_SECONDS=2 NR_ENABLED=1 NR_THRESHOLD=5 \
  ./venv/bin/python record.py 'sdrplay|scanmw' 1008000 am 12 /tmp/amtest.mp3

# Scanner
./venv/bin/python scan.py mw --dwell 0.25 --record-seconds 0
```

## Offene Punkte / Ideen

- Beobachten, ob der 90-s-DRM-Lock ausreicht (ggf. per `DRM_LOCK_SECONDS` tunen).
- Optional: `/docs/`-Route in der WebApp-Übersicht verlinken.

## Geänderte Dateien (Kurzliste)

- `/opt/radio-agent/rxdetect.py` (neu)
- `/opt/radio-agent/scan.py` (neu)
- `/opt/radio-agent/drm_fetch.py` (neu)
- `/opt/radio-agent/record.py` (v2)
- `/opt/radio-agent/noise.py`
- `/opt/radio-agent/daemon.py`
- `/opt/radio-agent/webapp.py` (`/docs/`-Route)
- `/opt/owrx-docker/var/settings.json` (Profil `sdrplay|scanmw`)
