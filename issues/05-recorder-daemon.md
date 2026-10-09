# 05 – Recorder-Daemon (Schedule -> mp3 + now.json + Retention)

Status: pending
Type: task
Blocked by: -

## Ziel
Dienst, der `deutsch.json` abarbeitet: Sprache > Fax, 10min-mp3-Chunks nach
`/archive/audio/<sender>/` (+ Sidecar), schreibt `/archive/now.json`
(für WebApp Kasten 1), Retention 1 Woche heiß -> Archiv -> 2 Monate löschen.

## Schritte
1. `record.py` als Modul (Tune+Capture, Chunk-Rotation, Dateiname nach Q6).
2. Prio-Logik + Überlappung (Verlierer loggen/überspringen).
3. Fax-Pfad: OWRX Background-FAX + Static-Schedule konfigurieren, Bilder von
   `/tmp` nach `/archive/fax/<sender>/` einsammeln.
4. systemd-Timer oder Cron, Logging, Healthcheck.
5. `now.json`-Writer (Sender/Freq/Mode/Seit) + Retention-Job.

## Done wenn
1 Nacht unbeaufsichtigt: mp3s + ggf. Faxbilder im Archiv, WebApp zeigt alles.
