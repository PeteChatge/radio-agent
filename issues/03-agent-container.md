# 03 – opencode-Container + Cron

Status: pending
Type: task
Blocked by: 01, API-Keys vom User (Zen-free + OpenRouter-5$)

## Ziel
`ghcr.io/anomalyco/opencode` als Docker neben `owrx-mbe` (gleiches Network),
Config `/opt/agent/` (`.env` 600), Host-Cron `docker exec … opencode run`.

## Schritte
1. Keys vom User erhalten → `/opt/agent/.env`.
2. Compose/Service anlegen, `serve` vs `run`-Test.
3. Ersten Cron-Job (Bookmark-Backup + Healthcheck) anlegen.

## Done wenn
`opencode run "…" ` läuft auf Pi non-interaktiv, Cron feuert.
