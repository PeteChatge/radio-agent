# 01 – Recorder-Probe (WS-Tune + 60s-mp3)

Status: resolved
Type: task

## Ziel
Risikoreichsten Teil zuerst beweisen: vom Pi aus OWRX per WebSocket tunen
und 60s Audio als mp3 capturen – ohne offenen Browser.

## Schritte
1. `docker port owrx-mbe`, Erreichbarkeit `localhost:8073` vs `192.168.1.147:8073` vom Pi.
2. WS-Handshake `ws://…:8073/ws/` (SERVER DE CLIENT…), `selectprofile` + `setfrequency` (z.B. DDK3 7878100 fax-Test oder 6020 AM).
3. Audio-Stream-URL finden (Netzwerk-Tab/Doku), 60s per `ffmpeg` → mp3, prüfen (Größe, abspielbar).
4. Ergebnis + exakte Befehle/URLs hier dokumentieren → Fixpunkt für Recorder-Service.

## Done wenn
60s-mp3 vom gewählten Sender liegt auf Pi + Befehlsfolge reproduzierbar.
