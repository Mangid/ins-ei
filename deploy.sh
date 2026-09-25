#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "== INS-EI Deploy =="
echo "[1/5] GitHub-Stand übernehmen"
git fetch origin main
# Falls nur dieses Deploy-Skript lokal geändert wurde, darf der Bootstrap es zurücksetzen.
git restore deploy.sh 2>/dev/null || true
# Repository-Code auf den freigegebenen main-Stand setzen. Persistente Daten und server/secrets liegen außerhalb der getrackten Codeänderungen.
git reset --hard origin/main

echo "[2/5] Frontend prüfen"
node --check server/frontend/app.js
node --check server/frontend/offline.js
node --check server/frontend/sw.js

echo "[3/5] Backend prüfen"
python3 -m py_compile server/backend/app.py

echo "[4/5] Docker Images bauen"
cd server
docker compose build ins-ei-api ins-ei-frontend

echo "[5/5] Dienste aktualisieren"
docker compose up -d --force-recreate ins-ei-api ins-ei-frontend
docker compose ps

echo "== Deploy erfolgreich =="
