# Deploying RallyGully Pulse

One process serves the API and the built UI, with a SQLite file as the database. That suits one server and a small
team. It means: **run exactly one worker** (the scheduler and cache live in the process), **keep the database file
on a disk that persists**, and **back it up**.

## 1. Server

A Linux VM (Ubuntu 22.04+), 1 vCPU / 1 GB is enough, with a domain name pointing at it (say `pulse.example.com`).
Install Python 3.10+, Node 20.19+, git and Caddy (or nginx) for HTTPS.

## 2. Get the code and build

```bash
sudo mkdir -p /opt/pulse && sudo chown $USER /opt/pulse
git clone <your repo url> /opt/pulse && cd /opt/pulse
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
(cd frontend && npm ci && npm run build)
```

## 3. Settings

Create `/opt/pulse/backend/.env.pulse` (readable by the service user only, `chmod 600`):

```
PULSE_SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(48))">
PULSE_APP_URL=https://pulse.example.com
PULSE_DATABASE_URL=sqlite:////opt/pulse/data/pulse.db
```

The app refuses to start with the default secret key on a non-local URL. Add the SMTP / WhatsApp / IMAP variables
from the README only for the integrations you use.

## 4. Create the database: history only, one admin

Do **not** run `app.seed` or `run.sh` on a live server: they create demo data and accounts with a known password.

```bash
cd /opt/pulse/backend && mkdir -p ../data && set -a && . ./.env.pulse && set +a
.venv/bin/python -m app.seed                      # creates venues, facilities and the demo accounts once
.venv/bin/python -m app.golive --confirm --history ../Historic_data_ingestion --bookings ../online_bookings
.venv/bin/python -m app.prune_users               # dry run: shows who goes
.venv/bin/python -m app.prune_users --confirm --reset-password   # keeps admin@rallygully.com, sets a new password
```

The seed is needed only to create the venues. `golive` then clears every demo booking, payment and customer and
imports your real history. `prune_users` removes every account except the HQ admin and replaces the `pulse1234`
password. Then add real people under **People** in the app (they get an invite link).

## 5. Run as a service

`/etc/systemd/system/pulse.service`:

```
[Unit]
Description=RallyGully Pulse
After=network.target

[Service]
User=pulse
WorkingDirectory=/opt/pulse/backend
EnvironmentFile=/opt/pulse/backend/.env.pulse
ExecStart=/opt/pulse/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd -r -s /usr/sbin/nologin pulse && sudo chown -R pulse /opt/pulse/data /opt/pulse/backend
sudo systemctl enable --now pulse && curl -s localhost:8000/api/health
```

## 6. HTTPS

Sessions are bearer tokens kept in the browser, so serve only over HTTPS. `/etc/caddy/Caddyfile`:

```
pulse.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

`sudo systemctl reload caddy` (Caddy gets the certificate itself). Open `https://pulse.example.com`, sign in as the
admin, and check **Portfolio** shows history.

## 7. Backups and updates

- Nightly copy of the database: `sqlite3 /opt/pulse/data/pulse.db ".backup '/backups/pulse-$(date +%F).db'"`.
- Update: `git pull && (cd frontend && npm ci && npm run build) && sudo systemctl restart pulse`.
  New columns are added on start; existing data is kept.

## Before you share the link

- The repository holds customer names (`online_bookings`, `Historic_data_ingestion`). Keep it **private**.
- Sign in as each role once after inviting people to check what they can see.
