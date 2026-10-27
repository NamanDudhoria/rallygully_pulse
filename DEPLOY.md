# Deploying RallyGully Pulse

Pulse is one Python server that also serves the website. The setup here is **one AWS EC2 server** (Amazon Linux
2023 or Ubuntu) running Pulse behind https, with **Supabase (PostgreSQL) as the database**. (Instead of Supabase
you can keep the database as a file on the server: the setup asks.) Run **exactly one instance** (the scheduler
and cache live in the process).

```
browser ──https──> EC2 server: Caddy (https) → Pulse (API + website) ──postgres──> Supabase
```

## Fastest path: one AWS EC2 server

You need a **domain you control** (the clock-in camera and location work only over https, and a certificate
can't be issued for a bare IP address).

1. **Launch the server.** AWS console → pick the region closest to your users (for India: Mumbai, `ap-south-1`)
   → EC2 → Launch instance: name `pulse`; image **Amazon Linux 2023** or **Ubuntu Server 24.04 LTS** (both are
   supported; Amazon Linux 2, the older one, is not); type **t3.small** (2 GB; a t3.micro also works, the script
   adds swap; ARM `t4g` works too); create a **key pair** and keep the `.pem` file; network: allow **SSH from My
   IP** and **HTTP and HTTPS from anywhere**; storage 20 GiB gp3.
2. **Elastic IP.** EC2 → Elastic IPs → Allocate → Associate with the instance, so the address survives a restart.
3. **DNS.** At your registrar (or Route 53) add an **A record**: `pulse.yourdomain.com` → the Elastic IP.
4. **Connect.** Easiest: select the instance → **Connect** → *EC2 Instance Connect* (a terminal in the browser, no
   key file). The login is `ec2-user` on Amazon Linux and `ubuntu` on Ubuntu. Or from PowerShell:
   `ssh -i <your-key-file>.pem ec2-user@<elastic-ip>` (use the real path to your `.pem`; anything in angle
   brackets is a placeholder).
5. **Get the code.** The repo is private, so give the server a read-only deploy key:
   ```bash
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
   cat ~/.ssh/id_ed25519.pub        # copy this line
   ```
   GitHub → the repo → Settings → Deploy keys → Add deploy key (leave "Allow write access" **off**), then:
   ```bash
   command -v git || sudo dnf install -y git          # Amazon Linux does not include git; Ubuntu does
   sudo mkdir -p /opt/pulse && sudo chown $USER /opt/pulse
   git clone git@github.com:NamanDudhoria/rallygully_pulse.git /opt/pulse     # answer "yes" to the fingerprint
   ```
6. **Run the setup script.**
   ```bash
   cd /opt/pulse && sudo bash deploy/ec2/setup.sh pulse.yourdomain.com
   ```
   It installs Python, Node and Caddy (standalone, checksum-verified downloads under `/opt/pulse-tools`, so it
   behaves the same on Amazon Linux and Ubuntu), builds the website, generates the secret key, starts Pulse as a
   service, turns on https and sets up the nightly backup of a server-side database. **It asks once where the
   database is:** paste your Supabase **Session pooler** connection string (Supabase → Project Settings →
   Database → Connection string → *Session pooler*; the real string with your password filled in, any `@ # / ?` in
   the password percent-encoded as `%40 %23 %2F %3F`), or press Enter to keep a database file on this server.
   It **tests the connection before saving anything**; if it can't connect it says why and you run it again. It
   can be re-run safely and never overwrites your settings or your data.
7. **Build the database, once, if it is new and empty.**
   ```bash
   sudo bash /opt/pulse/deploy/ec2/create-database.sh
   ```
   It creates the venues, imports your sales history and online bookings from the repository, removes the demo
   accounts, and asks you to choose the **HQ admin password** (`admin@rallygully.com`). It takes a few minutes
   (longer against a hosted database) and **refuses to run if the database already has users**, so it can't wipe
   live data by accident. If you already built the Supabase database from your PC, skip this step.
8. **Open** `https://pulse.yourdomain.com` (the first visit can take about a minute for the certificate) and sign in.
9. **Email** (clock-in photos, escalations): `sudo nano /opt/pulse/backend/.env.pulse`, uncomment and fill the
   `PULSE_SMTP_*` lines (for Google Workspace use an app password, port 587; AWS blocks outbound port 25 by
   default), then `sudo systemctl restart pulse`.
10. **Update later:** `sudo bash /opt/pulse/deploy/ec2/update.sh`.

### Backups: do not skip this

- **Supabase database:** Supabase's own backups apply, and they depend on your plan (check Supabase → Database →
  Backups; free projects may have none). If you have no plan-level backup, take a copy yourself with `pg_dump` on a
  schedule you keep an eye on, before risky changes at least.
- **Database file on this server:** copied automatically every night into `/opt/pulse/backups` (kept 14 days,
  integrity-checked; check the schedule with `systemctl list-timers pulse-backup`). That protects against
  mistakes, **not** against losing the server: also snapshot the disk in AWS (EC2 → Lifecycle Manager → Create
  lifecycle policy → *EBS snapshot policy*, target the instance, daily, keep 14). To restore a copy:
  `sudo systemctl stop pulse`, copy the file over `/opt/pulse/data/pulse.db` (owner `pulse`), `sudo systemctl start pulse`.

Troubleshooting: the setup script stops with a message naming what failed; re-run it after fixing that. Site doesn't load → the DNS record and the security group (ports 80 and 443);
`sudo journalctl -u caddy -n 50`. Gateway error (502) → `sudo journalctl -u pulse -n 50`. Changed a setting →
edit `.env.pulse`, then `sudo systemctl restart pulse`.

How it's laid out: your login owns the code and runs `git pull` and the builds; the service runs as a separate
`pulse` user that can read the code but change only the database, its backups and the mail inbox; the settings
file is readable only by root and that service.

## Notes for a hosted database

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

- Set each venue's **map position** (Configuration → Venues → edit → Map position). Without it, Venue Managers
  cannot clock in and their venue stays closed to them.
- Set `PULSE_SMTP_HOST`, `PULSE_SMTP_USER`, `PULSE_SMTP_PASSWORD` (and optionally `PULSE_ATTENDANCE_EMAIL`), or
  clock-in photos are held in the database and never reach Operations.
- The repository holds customer names (`online_bookings`, `Historic_data_ingestion`). Keep it **private**.
- Sign in as each role once after inviting people, to check what each can see.
