#!/usr/bin/env bash
# Update a running Pulse to the latest main: sudo bash /opt/pulse/deploy/ec2/update.sh
set -euo pipefail
APP=/opt/pulse
TOOLS=/opt/pulse-tools
export PATH="/usr/local/bin:$PATH"
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }
OWNER="${SUDO_USER:-}"
[ -n "$OWNER" ] && [ "$OWNER" != root ] || { echo "Run it with sudo from your normal login (ubuntu / ec2-user), not as root." >&2; exit 1; }
sudo -u "$OWNER" env "PATH=$PATH" "UV_PYTHON_INSTALL_DIR=$TOOLS/python" UV_PYTHON_PREFERENCE=only-managed \
  bash -euo pipefail <<UPD
cd "$APP"
git pull --ff-only origin main
uv pip install --python backend/.venv/bin/python -q -r backend/requirements.txt
cd frontend && npm ci --no-progress --loglevel=error && npm run build
test -f dist/index.html
UPD
# New files from git must stay readable by the service; its data, backups and inbox are left alone.
find "$APP" \( -path "$APP/data" -o -path "$APP/backups" -o -path "$APP/backend/inbox" \) -prune -o -exec chmod g+rX {} +
for f in pulse.service pulse-backup.service pulse-backup.timer caddy.service; do install -m 644 "$APP/deploy/ec2/$f" "/etc/systemd/system/$f"; done
systemctl daemon-reload
systemctl restart pulse
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && { echo "Updated and running: $(git -C $APP log --oneline -1)"; exit 0; }; sleep 1; done
journalctl -u pulse -n 30 --no-pager; echo "Pulse did not come back after the update." >&2; exit 1
