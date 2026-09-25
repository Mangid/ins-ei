#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "== INS-EI Deploy =="
echo "[1/5] Git aktualisieren"
git fetch origin main
git pull --ff-only origin main

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
