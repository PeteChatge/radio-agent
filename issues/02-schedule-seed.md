# 02 – Schedule-Seed deutsch.json

Status: resolved (Seed 16 Eintraege, A26; Refresh -> Agent-Cron Ticket 03)
Type: task
Blocked by: 01

## Ziel
Erste Wahrheitstabelle `/archive/schedule/deutsch.json`:
`{sender, sprache, freqHz, mode, startUTC, endeUTC, tage, quelle, prio}`.

## Schritte
1. Seed aus aktiven Bookmarks (Israel 6020, China 11880, Magyar 541, Böhmen 792)
   + DDK3/DDH6-Fax-Slots (Tag/Nacht).
2. Erste Internet-Recherche (EIBI/Aoki): deutsche Aussendungen mit Zeiten,
   mehrsprachige Sender als mehrere Einträge.
3. Wöchentlicher Refresh-Job (Cron-Skizze).

## Done wenn
`deutsch.json` validiert, mind. 8 Einträge mit Quelle + UTC-Zeiten.
