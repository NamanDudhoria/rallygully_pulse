#!/usr/bin/env bash
# Set up RallyGully Pulse on a fresh Ubuntu 22.04 / 24.04 server (an EC2 instance).
#
#   git clone <repo> /opt/pulse && cd /opt/pulse
#   sudo bash deploy/ec2/setup.sh pulse.example.com
#
# Safe to run again: it only fills in what is missing and never overwrites your settings file.
set -euo pipefail

DOMAIN="${1:-}"
APP=/opt/pulse
ENV_FILE="$APP/backend/.env.pulse"

die() { echo "ERROR: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

[ "$(id -u)" -eq 0 ] || die "Run with sudo: sudo bash deploy/ec2/setup.sh pulse.example.com"
OWNER="${SUDO_USER:-}"   # your login: owns the code, runs git pull and the builds
[ -n "$OWNER" ] && [ "$OWNER" != root ] || die "Run it with sudo from your normal login (e.g. ubuntu), not as root."
[ -n "$DOMAIN" ] || die "Give the domain that points at this server: sudo bash deploy/ec2/setup.sh pulse.example.com"
[[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || die "'$DOMAIN' doesn't look like a domain name (no https://, no path)"
[ -f "$APP/backend/app/main.py" ] || die "Pulse isn't checked out at $APP. Clone it there first (see DEPLOY.md)."
command -v apt-get >/dev/null || die "This script is for Ubuntu / Debian."

step "Checking that $DOMAIN points at this server"
HERE_IP="$(curl -fsS --max-time 8 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
DNS_IP="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}' || true)"
if [ -n "$HERE_IP" ] && [ "$DNS_IP" = "$HERE_IP" ]; then
  echo "OK: $DOMAIN -> $DNS_IP"
else
  echo "WARNING: $DOMAIN resolves to '${DNS_IP:-nothing}' but this server is '${HERE_IP:-unknown}'."
  echo "         HTTPS can't be issued until the DNS A record points here (and ports 80 and 443 are open)."
  read -r -p "Continue anyway? [y/N] " yn; [[ "$yn" =~ ^[Yy]$ ]] || exit 1
fi

step "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip git curl cron ca-certificates gnupg debian-keyring debian-archive-keyring apt-transport-https >/dev/null

if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]; then
  step "Installing Node.js 22 (needed to build the UI)"
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null
  apt-get install -y -qq nodejs >/dev/null
fi

if ! command -v caddy >/dev/null; then
  step "Installing Caddy (HTTPS)"
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy >/dev/null
fi

if [ "$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)" -lt 1500 ] && [ "$(swapon --show --noheadings | wc -l)" -eq 0 ]; then
  step "Adding 2 GB swap (small server: protects the UI build)"
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

step "Creating the service user (it can read the code and write only the mail inbox)"
id pulse >/dev/null 2>&1 || useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin pulse
chown -R "$OWNER:pulse" "$APP"
chmod -R g+rX "$APP"
install -d -o pulse -g pulse "$APP/backend/inbox" "$APP/backend/inbox/processed"
# The database and its backups live outside the code, owned by the service (setup never changes them on a re-run).
install -d -o pulse -g pulse -m 750 "$APP/data" "$APP/backups"

step "Installing Pulse (Python packages and the UI)"
sudo -u "$OWNER" bash -euo pipefail <<INSTALL
cd "$APP"
python3 -m venv backend/.venv
backend/.venv/bin/pip install -q --upgrade pip
backend/.venv/bin/pip install -q -r backend/requirements.txt
cd frontend && npm ci --no-progress --loglevel=error && npm run build
test -f dist/index.html
INSTALL

if [ ! -f "$ENV_FILE" ]; then
  step "Settings (stored only in $ENV_FILE, readable by root and the service only)"
  umask 077
  cat > "$ENV_FILE" <<ENVF
# The database is a file on this server. (To use a hosted PostgreSQL such as Supabase instead, replace this line
# with its postgresql:// connection string.)
PULSE_DATABASE_URL=sqlite:///$APP/data/pulse.db
PULSE_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
PULSE_APP_URL=https://$DOMAIN

# Email (clock-in photos, escalations). Fill in, then: sudo systemctl restart pulse
#PULSE_SMTP_HOST=smtp.gmail.com
#PULSE_SMTP_PORT=587
#PULSE_SMTP_USER=
#PULSE_SMTP_PASSWORD=
#PULSE_OPS_EMAIL=ops@rallygully.com
#PULSE_ATTENDANCE_EMAIL=
ENVF
  chown root:pulse "$ENV_FILE"; chmod 640 "$ENV_FILE"
else
  echo "Keeping your existing settings file: $ENV_FILE"
fi

step "Scheduling a nightly database backup (kept 14 days in $APP/backups)"
cat > /etc/cron.d/pulse-backup <<CRON
# 02:30 India time (21:00 UTC) every night
0 21 * * * pulse bash -c 'set -a; . $ENV_FILE; set +a; exec $APP/backend/.venv/bin/python $APP/deploy/ec2/backup.py' >> $APP/backups/backup.log 2>&1
CRON
chmod 644 /etc/cron.d/pulse-backup
systemctl enable --now cron >/dev/null 2>&1 || true

step "Starting the service"
install -m 644 "$APP/deploy/ec2/pulse.service" /etc/systemd/system/pulse.service
systemctl daemon-reload
systemctl enable pulse >/dev/null
systemctl restart pulse
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break; sleep 1; done
curl -fsS http://127.0.0.1:8000/api/health >/dev/null || { journalctl -u pulse -n 30 --no-pager; die "Pulse did not start. The log above shows why."; }
echo "Pulse is running."

step "Turning on HTTPS for $DOMAIN"
sed "s/__DOMAIN__/$DOMAIN/" "$APP/deploy/ec2/Caddyfile.template" > /etc/caddy/Caddyfile
systemctl enable caddy >/dev/null
systemctl reload caddy 2>/dev/null || systemctl restart caddy

cat <<DONE

Done. Open  https://$DOMAIN  (the first visit can take about a minute while the certificate is issued).

The database is still empty. Build it once, with your sales history and bookings:
  sudo bash $APP/deploy/ec2/create-database.sh

  Logs:     sudo journalctl -u pulse -f      (app)     sudo journalctl -u caddy -f   (HTTPS)
  Settings: sudo nano $ENV_FILE  then  sudo systemctl restart pulse
  Update:   sudo bash $APP/deploy/ec2/update.sh
  Backups:  $APP/backups  (nightly, 14 days). Also snapshot the server's disk in AWS: see DEPLOY.md.
DONE
