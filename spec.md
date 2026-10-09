# Spec: Radio-Agent (Pi4 + OpenWebRX+)

Stand: 2026-10-09, gegrillt + Shared Understanding bestätigt.

## Ziel
Autonomer Betrieb auf Raspberry Pi 4 (Debian, 4GB, 117G-SD, 84G frei) mit
Docker `slechev/openwebrxplus-softmbe` (`owrx-mbe`, v1.2.125, Dresden JO61va,
Nooelec v5 + RSP1A). Ohne PC: per MCP/Agent Radio verbessern, Content sammeln,
zeitgesteuerte Aufnahmen. Anzeige via Handy-WebApp.

## Geltende Entscheidungen
- Zugriff: SSH `olaf@raspi` (Key `opencode_pi`), User in `docker`+`sudo`, sudo NOPASSWD. Kein root-Login.
- Owrx-Files: Bind-Mounts `/opt/owrx-docker/{etc,var,plugins}`. Owner-Artefakt
  `103:104 (statd:avahi)` = Container-interner Writer → bei Restore `chown --reference`
  + `chmod 644`, nur bei gestopptem Container. Container läuft als root.
- Bookmarks aktiv: Restore vom 2026-02-18 durchgeführt (11 Einträge: DDK3/DDH6 Fax,
  Israel 6020, China 11880, Magyar 541, Böhmen 792, FreeDV, Sprechfunk). Leere
  aktive Datei wurde nach `~/bookmarks.json.empty-20261009` gesichert.
- Prio: **Sprache > Fax** (Fax wiederholt 2–3x), dann seltene Deutsch-Sender,
  Standard, Scan-Lückenfüller. 2 SDRs → Fax + Sprache parallel möglich.
- Fax: DDK3 7880 Tag + DDH 3855 Nacht (RSP1A 49m/80m), Background-FAX +
  Static-Schedule, Daylight-Fallback. Ablage `/archive/fax/<sender>/`.
- Audio: 10min 64k mono mp3 + `.json`-Sidecar, `/archive/audio/<sender>/`.
  Heiß 1 Woche → Archiv → nach 2 Monaten löschen. Bilder + Audio getrennt,
  pro Sender. Bookmark-Backups versioniert, nur Originale archivieren.
- Scan: RSP1A 49/41/31/25/22m + MW + 80m, nur scannable-Modi, SNR/Dauer-Gate,
  Dedupe ±500Hz, Backup vor Write.
- Recherche: JSON pro Aussendung
  `{sender, sprache, freqHz, mode, startUTC, endeUTC, tage, quelle, prio}`,
  wöchentlich per Fetch (EIBI/Aoki/DRM), mehrsprachige Sender + Frequenzwechsel
  als mehrere Einträge. Wahrheit: `/archive/schedule/deutsch.json`.
- Recorder: eigener Python-Service (Docker, gleiches Network): liest Schedule,
  WS-Tune (`setfrequency/selectprofile`), ffmpeg-Audio → mp3-Chunks.
  OWRX-Scheduler für Fax-Background, Recorder für Sprache/DRM. Reines
  JS-Plugin reicht nicht (wirkt nur bei offenem Browser).
- Agent: `opencode` Docker `ghcr.io/anomalyco/opencode`, Config `/opt/agent/`
  (`.env`: Zen-free primär, OpenRouter-5$ fallback), Host-Cron
  `docker exec … opencode run`. Erst ohne `web`, später Tailscale.
- Handy-WebApp: `index.html` (GitHub-konform), Port **18088**, 3 Kästen
  (Jetzt aktiv / Dateiliste 🎵📠 / Cron-Liste), Walkman-Look mit
  Play/Stop/Next/Repeat, v1 Anzeige + lokale Playback-Controls.
- Storage: bis Weihnachts-SSD SD schonen (Retention wie oben, Logrotate).
  EIBI-Daten (`eibi.json` 3MB) liegen bereits in `/var/lib/openwebrx`.

## Referenzen
- Radio: http://192.168.1.147:8073 (N0CALL, v1.2.125)
- Pi: `olaf@192.168.1.147`, Key `~/.ssh/opencode_pi`
- Backup: `/opt/owrx-docker_backup_20260218/var/bookmarks.json`
- wfax-Liste: `/opt/owrx-docker/etc/bookmarks.d/wfax.json` (DDH3/DDK3/DDK6, GYA…)
- `de/`: nur `cb.json` + `freenet.json` (kein MW/KW-Rundfunk yet)
