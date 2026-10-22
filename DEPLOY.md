# Deploying RallyGully Pulse (Supabase database)

Supabase hosts the **database only**. Pulse is a Python server that also serves the UI, so it needs somewhere to
run: any Linux VM, or a host that runs one long-lived Python process. Run **exactly one instance** (the
scheduler and cache live in the process).

```
browser ──https──> your app host (Pulse: API + UI) ──postgres──> Supabase
```

## Put the app host and the database in the same region

Pulse's busiest pages run about 30 to 60 database queries each in sequence (Portfolio 56, venue day 32, venue
grid 57; today's Portfolio about 105 on its first load; measured on Postgres 16 with this data). Every query waits
for the network, so the distance to Supabase sets the page time: about 0.06 s extra at 1 ms, 0.3 s at 5 ms, 1.8 s at
30 ms and 9 s at 150 ms (60 queries). Pick the Supabase region first, then create the VM in the **same region**
(ideally the same city). Round-trip times are typical figures, not measured; the arithmetic is exact. If a page
is slow, test with `psql "<connection string>" -c "select 1"` from the host and time it.

## 1. Supabase

1. In your project: **Project Settings → Database → Connection string**, choose **Session pooler** (port 5432) and
   copy the URI. It looks like
   `postgresql://postgres.<project-ref>:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres`.
   The pooler is the one that works from IPv4-only hosts; the "direct" host is IPv6-only unless you buy the add-on.
2. Put your database password in. If it contains `@ : / # ?`, percent-encode them (`@` becomes `%40`).
3. Pulse turns on row-level security for every table it creates, so Supabase's public API keys cannot read your
   data. Pulse itself connects as the database owner and is unaffected. Do not hand out the project's `service_role`
   key and keep the Data API off if you don't use it.

## 2. Build the live database (from your PC, once)

This needs the Excel folders, so do it where the repository is checked out. In PowerShell:

```
cd D:\RallyGully\rallygully-pulse\backend
pip install -r requirements.txt
$env:PULSE_DATABASE_URL = "postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres"
python -m app.seed
python -m app.golive --confirm --history ..\Historic_data_ingestion --bookings ..\online_bookings
python -m app.prune_users
python -m app.prune_users --confirm --reset-password
```

- `app.seed` only exists to create the venues and their courts; it also writes demo bookings, which `golive`
  removes straight after. Over the internet it can take several minutes.
- `golive` and `prune_users` print `Database: ...` first. **Read it**: `golive --confirm` deletes bookings and
  payments, so never run it against the live database once real operations have started.
- The first `prune_users` is a dry run. The second keeps only `admin@rallygully.com` and asks for a new password.
- There is no automatic backup on Postgres. Take one first if the database holds anything you care about.

Check in Supabase **Table Editor**: `users` has one row; `hist_revenue` has about 5,000 rows;
`hist_bookings` about 10,000.

## 3. App host (Linux VM example)

```bash
sudo mkdir -p /opt/pulse && sudo chown $USER /opt/pulse
git clone <your private repo url> /opt/pulse && cd /opt/pulse
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
(cd frontend && npm ci && npm run build)
```

`/opt/pulse/backend/.env.pulse` (`chmod 600`):

```
PULSE_DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
PULSE_SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(48))">
PULSE_APP_URL=https://pulse.example.com
```

The app refuses to start with the default secret key on a non-local URL. Add the SMTP / WhatsApp / IMAP
variables from the README only for integrations you use.

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
sudo useradd -r -s /usr/sbin/nologin pulse && sudo chown -R pulse /opt/pulse/backend
sudo systemctl enable --now pulse && curl -s localhost:8000/api/health
```

## 4. HTTPS

Sessions are bearer tokens kept in the browser, so serve only over HTTPS. `/etc/caddy/Caddyfile`:

```
pulse.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

`sudo systemctl reload caddy`, then open `https://pulse.example.com` and sign in as the admin.

## 5. Backups and updates

- Backups: check what your Supabase plan includes (free projects may have none). Either way, schedule your own:
  `pg_dump "<connection string>" -Fc -f pulse-$(date +%F).dump`.
- Update: `git pull && (cd frontend && npm ci && npm run build) && sudo systemctl restart pulse`. New nullable
  columns are added on start; existing data is kept.

## Before you share the link

- The repository holds customer names (`online_bookings`, `Historic_data_ingestion`). Keep it **private**.
- Sign in as each role once after inviting people, to check what each can see.

## Single-server alternative

Without Supabase, leave `PULSE_DATABASE_URL` unset and Pulse uses a SQLite file (`sqlite:////opt/pulse/data/pulse.db`
on a persistent disk). Same steps, minus section 1; `golive` and `prune_users` then take a file backup for you.
