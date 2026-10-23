#!/usr/bin/env bash
# Update a running Pulse to the latest main: sudo bash /opt/pulse/deploy/ec2/update.sh
set -euo pipefail
APP=/opt/pulse
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }
OWNER="${SUDO_USER:-}"
[ -n "$OWNER" ] && [ "$OWNER" != root ] || { echo "Run it with sudo from your normal login (e.g. ubuntu), not as root." >&2; exit 1; }
sudo -u "$OWNER" bash -euo pipefail <<UPD
cd "$APP"
git pull --ff-only origin main
backend/.venv/bin/pip install -q -r backend/requirements.txt
cd frontend && npm ci --no-progress --loglevel=error && npm run build
test -f dist/index.html
UPD
chmod -R g+rX "$APP"
systemctl restart pulse
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && { echo "Updated and running: $(git -C $APP log --oneline -1)"; exit 0; }; sleep 1; done
journalctl -u pulse -n 30 --no-pager; echo "Pulse did not come back after the update." >&2; exit 1
