#!/usr/bin/env bash
# Build the live database on this server, once: venues, your sales history and online bookings, one admin.
#
#   sudo bash /opt/pulse/deploy/ec2/create-database.sh
#
# Refuses to run if the database already has users, because it replaces everything in it.
set -euo pipefail
APP=/opt/pulse
ENV_FILE="$APP/backend/.env.pulse"
die() { echo "ERROR: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

[ "$(id -u)" -eq 0 ] || die "Run with sudo."
[ -f "$ENV_FILE" ] || die "Run deploy/ec2/setup.sh first."
grep -q '^PULSE_DATABASE_URL=sqlite:///' "$ENV_FILE" || die "This script builds a database on this server (SQLite); your settings point elsewhere."
[ -d "$APP/Historic_data_ingestion" ] && [ -d "$APP/online_bookings" ] || die "The Historic_data_ingestion / online_bookings folders are missing from $APP."

# Run Python as the service user, with the service's settings, in the data folder (reports are written there).
as_pulse() {
  sudo -u pulse env -i HOME=/nonexistent PATH=/usr/bin:/bin PYTHONPATH="$APP/backend" \
    bash -c 'set -a; . "$0"; set +a; cd "$1" && shift && exec "$@"' "$ENV_FILE" "$APP/data" "$APP/backend/.venv/bin/python" "$@"
}

USERS="$(as_pulse -c "
from sqlalchemy import select, func
from app.db import SessionLocal, ensure_schema
from app.models import User
ensure_schema(); db = SessionLocal(); print(db.scalar(select(func.count()).select_from(User)))")"
[ "$USERS" = "0" ] || die "The database already has $USERS user(s). This script replaces everything in it; stopping to protect your data."

step "Stopping the app while the database is built"
systemctl stop pulse

step "Creating venues, then clearing the demo data and importing your sales history and bookings (a few minutes)"
as_pulse -m app.seed >/dev/null
as_pulse -m app.golive --confirm --history "$APP/Historic_data_ingestion" --bookings "$APP/online_bookings"

step "Removing the demo accounts; keeping only the HQ admin (you choose the new password)"
as_pulse -m app.prune_users
as_pulse -m app.prune_users --confirm --reset-password

step "Starting the app"
systemctl start pulse
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break; sleep 1; done
curl -fsS http://127.0.0.1:8000/api/health >/dev/null || { journalctl -u pulse -n 30 --no-pager; die "Pulse did not start."; }
echo
echo "Done. Sign in as admin@rallygully.com with the password you just set."
echo "Reports of what was imported: $APP/data/history_report.md and $APP/data/bookings_report.md"
