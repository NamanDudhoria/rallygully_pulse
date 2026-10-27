#!/usr/bin/env bash
# Set up RallyGully Pulse on a fresh server: Amazon Linux 2023 or Ubuntu 22.04 / 24.04 (x86 or ARM).
#
#   git clone <repo> /opt/pulse && cd /opt/pulse
#   sudo bash deploy/ec2/setup.sh pulse.example.com
#
# Python, Node and Caddy are installed as standalone, checksum-verified downloads under /opt/pulse-tools, so the
# steps are the same on every distribution. Safe to run again: it only fills in what is missing and never
# overwrites your settings file or your database.
set -euo pipefail

DOMAIN="${1:-}"
APP=/opt/pulse
TOOLS=/opt/pulse-tools
ENV_FILE="$APP/backend/.env.pulse"
NODE_VERSION=22.22.0
CADDY_VERSION=2.10.2
PYTHON_VERSION=3.12
export PATH="/usr/local/bin:$PATH"

die() { echo "ERROR: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

[ "$(id -u)" -eq 0 ] || die "Run with sudo: sudo bash deploy/ec2/setup.sh pulse.example.com"
OWNER="${SUDO_USER:-}"   # your login: owns the code, runs git pull and the builds
[ -n "$OWNER" ] && [ "$OWNER" != root ] || die "Run it with sudo from your normal login (ubuntu / ec2-user), not as root."
[ -n "$DOMAIN" ] || die "Give the domain that points at this server: sudo bash deploy/ec2/setup.sh pulse.example.com"
[[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || die "'$DOMAIN' doesn't look like a domain name (no https://, no path)"
[ -f "$APP/backend/app/main.py" ] || die "Pulse isn't checked out at $APP. Clone it there first (see DEPLOY.md)."
command -v systemctl >/dev/null || die "This script needs systemd (Amazon Linux 2023 or Ubuntu)."

. /etc/os-release
case "${ID:-}" in
  ubuntu|debian) PKG=apt ;;
  amzn) [ "${VERSION_ID:-}" = "2023" ] || die "Amazon Linux $VERSION_ID is too old (its libraries can't run Node 22). Use Amazon Linux 2023 or Ubuntu 24.04."; PKG=dnf ;;
  *) die "Unsupported system '${ID:-unknown}'. Use Amazon Linux 2023 or Ubuntu 22.04 / 24.04." ;;
esac
case "$(uname -m)" in
  x86_64)  NODE_ARCH=x64;   CADDY_ARCH=amd64; UV_ARCH=x86_64 ;;
  aarch64) NODE_ARCH=arm64; CADDY_ARCH=arm64; UV_ARCH=aarch64 ;;
  *) die "Unsupported CPU $(uname -m)." ;;
esac

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

step "Checking base tools"
MISSING=()
for t in git curl tar xz gzip sha256sum sha512sum; do command -v "$t" >/dev/null || MISSING+=("$t"); done
if [ "${#MISSING[@]}" -gt 0 ]; then
  echo "Installing: ${MISSING[*]}"
  if [ "$PKG" = apt ]; then
    apt-get update -qq
    apt-get install -y -qq "${MISSING[@]/xz/xz-utils}" ca-certificates >/dev/null
  else
    dnf install -y -q "${MISSING[@]/sha256sum/coreutils}" >/dev/null
  fi
fi

install -d -m 755 "$TOOLS"
fetch() { curl -fsSL --retry 3 --max-time 600 -o "$2" "$1" || die "Download failed: $1"; }

if [ "$("$TOOLS/node/bin/node" --version 2>/dev/null || true)" != "v$NODE_VERSION" ]; then
  step "Installing Node.js $NODE_VERSION (builds the website)"
  T="$(mktemp -d)"; F="node-v$NODE_VERSION-linux-$NODE_ARCH.tar.xz"
  fetch "https://nodejs.org/dist/v$NODE_VERSION/$F" "$T/$F"
  fetch "https://nodejs.org/dist/v$NODE_VERSION/SHASUMS256.txt" "$T/SUMS"
  (cd "$T" && grep " $F\$" SUMS | sha256sum -c - >/dev/null) || die "Node download failed its checksum."
  rm -rf "$TOOLS/node" && mkdir "$TOOLS/node" && tar -xJf "$T/$F" -C "$TOOLS/node" --strip-components=1 && rm -rf "$T"
fi
for b in node npm npx; do ln -sf "$TOOLS/node/bin/$b" "/usr/local/bin/$b"; done

if ! command -v uv >/dev/null; then
  step "Installing uv (fetches Python $PYTHON_VERSION and the app's packages)"
  T="$(mktemp -d)"; F="uv-$UV_ARCH-unknown-linux-gnu.tar.gz"
  fetch "https://github.com/astral-sh/uv/releases/latest/download/$F" "$T/$F"
  fetch "https://github.com/astral-sh/uv/releases/latest/download/$F.sha256" "$T/$F.sha256"
  (cd "$T" && sha256sum -c "$F.sha256" >/dev/null) || die "uv download failed its checksum."
  tar -xzf "$T/$F" -C "$T" && install -m 755 "$T/uv-$UV_ARCH-unknown-linux-gnu/uv" /usr/local/bin/uv && rm -rf "$T"
fi

if [ "$(/usr/local/bin/caddy version 2>/dev/null | awk '{print $1}')" != "v$CADDY_VERSION" ]; then
  step "Installing Caddy $CADDY_VERSION (https)"
  T="$(mktemp -d)"; F="caddy_${CADDY_VERSION}_linux_${CADDY_ARCH}.tar.gz"
  fetch "https://github.com/caddyserver/caddy/releases/download/v$CADDY_VERSION/$F" "$T/$F"
  fetch "https://github.com/caddyserver/caddy/releases/download/v$CADDY_VERSION/caddy_${CADDY_VERSION}_checksums.txt" "$T/SUMS"
  (cd "$T" && grep " $F\$" SUMS | sha512sum -c - >/dev/null) || die "Caddy download failed its checksum."
  tar -xzf "$T/$F" -C "$T" caddy && install -m 755 "$T/caddy" /usr/local/bin/caddy && rm -rf "$T"
fi
id caddy >/dev/null 2>&1 || useradd --system --home-dir /var/lib/caddy --create-home --shell /usr/sbin/nologin caddy
install -d -m 755 /etc/caddy

if [ "$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)" -lt 1500 ] && [ "$(swapon --show --noheadings | wc -l)" -eq 0 ]; then
  step "Adding 2 GB swap (small server: protects the website build)"
  dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

step "Creating the service user (it can read the code; it can change only the database, backups and mail inbox)"
id pulse >/dev/null 2>&1 || useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin pulse
# The code is yours and readable by the service. The database, its backups and the mail inbox belong to the service
# and are skipped here, so a re-run can never change who owns (and can write) your data.
fix_perms() {
  local skip=( \( -path "$APP/data" -o -path "$APP/backups" -o -path "$APP/backend/inbox" \) -prune -o )
  find "$APP" "${skip[@]}" -exec chown "$OWNER:pulse" {} +
  find "$APP" "${skip[@]}" -exec chmod g+rX {} +
}
fix_perms
install -d -o pulse -g pulse "$APP/backend/inbox" "$APP/backend/inbox/processed"
install -d -o pulse -g pulse -m 750 "$APP/data" "$APP/backups"
install -d -o "$OWNER" -g "$OWNER" -m 755 "$TOOLS/python"

step "Installing Pulse (Python packages and the website)"
sudo -u "$OWNER" env "PATH=$PATH" "UV_PYTHON_INSTALL_DIR=$TOOLS/python" UV_PYTHON_PREFERENCE=only-managed \
  bash -euo pipefail <<INSTALL
cd "$APP"
uv venv --python $PYTHON_VERSION --allow-existing backend/.venv
uv pip install --python backend/.venv/bin/python -q -r backend/requirements.txt
(cd backend && .venv/bin/python -c "import app.main" >/dev/null)   # fail here, not at start-up, if a package is missing
cd frontend && npm ci --no-progress --loglevel=error && npm run build
test -f dist/index.html
INSTALL
fix_perms

if [ ! -f "$ENV_FILE" ]; then
  step "Database and settings (stored only in $ENV_FILE, readable by root and the service only)"
  echo "Where is the database?"
  echo "  - Paste your Supabase 'Session pooler' connection string (real values, no <placeholders>), or"
  echo "  - press Enter to keep the database as a file on this server."
  read -r -s -p "Database URL (hidden; Enter = this server): " DB_URL; echo
  if [ -z "$DB_URL" ]; then
    DB_URL="sqlite:///$APP/data/pulse.db"
    echo "Using a database file on this server: $APP/data/pulse.db"
  else
    [[ "$DB_URL" == postgres*://* ]] || die "That doesn't look like a postgresql:// connection string."
    [[ "$DB_URL" != *"<"* && "$DB_URL" != *">"* && "$DB_URL" != *"["* ]] || die "The URL still contains a placeholder (<...> or [...]). Copy the real string from Supabase."
    echo "Testing the connection..."
    if ! OUT="$(sudo -u "$OWNER" env "PATH=$PATH" "PULSE_DATABASE_URL=$DB_URL" bash -c "cd '$APP/backend' && .venv/bin/python -c \"
from app.db import engine
with engine.connect() as c:
    c.exec_driver_sql('select 1')
print('Connected to', engine.url.render_as_string(hide_password=True))
\"" 2>&1)"; then
      echo "$OUT" | tail -4 | cut -c1-300
      die "Couldn't connect to that database (see above). Check the string and run this script again: nothing has been saved yet."
    fi
    echo "$OUT" | tail -1
  fi
  umask 077
  cat > "$ENV_FILE" <<ENVF
PULSE_DATABASE_URL=$DB_URL
PULSE_SECRET_KEY=$(head -c 48 /dev/urandom | base64 -w0 | tr '+/' '-_' | tr -d '=')
PULSE_APP_URL=https://$DOMAIN

# Email (clock-in photos, escalations). Fill in, then: sudo systemctl restart pulse
#PULSE_SMTP_HOST=smtp.gmail.com
#PULSE_SMTP_PORT=587
#PULSE_SMTP_USER=
#PULSE_SMTP_PASSWORD=
#PULSE_TWILIO_ACCOUNT_SID=
#PULSE_TWILIO_AUTH_TOKEN=
#PULSE_TWILIO_WHATSAPP_FROM=+91xxxxxxxxxx
#PULSE_TWILIO_CONTENT_SID_BOOKING=HX...
#PULSE_OPS_EMAIL=ops@rallygully.com
#PULSE_ATTENDANCE_EMAIL=
ENVF
else
  echo "Keeping your existing settings file: $ENV_FILE"
fi
chown root:pulse "$ENV_FILE"; chmod 640 "$ENV_FILE"   # secrets: root and the service only, on every run

step "Starting the service, the nightly backup (kept 14 days in $APP/backups) and https"
install -m 644 "$APP/deploy/ec2/pulse.service" /etc/systemd/system/pulse.service
install -m 644 "$APP/deploy/ec2/pulse-backup.service" /etc/systemd/system/pulse-backup.service
install -m 644 "$APP/deploy/ec2/pulse-backup.timer" /etc/systemd/system/pulse-backup.timer
install -m 644 "$APP/deploy/ec2/caddy.service" /etc/systemd/system/caddy.service
sed "s/__DOMAIN__/$DOMAIN/" "$APP/deploy/ec2/Caddyfile.template" > /etc/caddy/Caddyfile
chmod 644 /etc/caddy/Caddyfile  # the caddy service user must be able to read it
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || die "The generated Caddyfile is invalid."
systemctl daemon-reload
systemctl enable pulse pulse-backup.timer caddy >/dev/null 2>&1
systemctl restart pulse
systemctl start pulse-backup.timer
for i in $(seq 1 150); do curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break; sleep 1; done
curl -fsS http://127.0.0.1:8000/api/health >/dev/null || { journalctl -u pulse -n 30 --no-pager; die "Pulse did not start. The log above shows why."; }
echo "Pulse is running."
systemctl restart caddy

cat <<DONE

Done. Open  https://$DOMAIN  (the first visit can take about a minute while the certificate is issued).

If the database is new and empty, build it once with your sales history and bookings (it refuses if it already
has users, so it is safe to try):
  sudo bash $APP/deploy/ec2/create-database.sh

  Logs:     sudo journalctl -u pulse -f      (app)     sudo journalctl -u caddy -f   (https)
  Settings: sudo nano $ENV_FILE  then  sudo systemctl restart pulse
  Update:   sudo bash $APP/deploy/ec2/update.sh
  Backups:  a database file on this server is copied nightly to $APP/backups (14 days) and should also be snapshotted
            in AWS; a hosted database (Supabase) is backed up by the provider: see DEPLOY.md.
DONE
