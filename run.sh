#!/usr/bin/env bash
# One-command local run: installs deps, seeds demo data (first run), builds the UI,
# and serves app + API on http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv backend/.venv
backend/.venv/bin/pip install -q -r backend/requirements.txt
(cd frontend && npm install --silent && npm run build --silent)
cd backend
[ -f pulse.db ] || .venv/bin/python -m app.seed
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
