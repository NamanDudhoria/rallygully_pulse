# RallyGully Pulse

Internal venue operating system and performance intelligence for RallyGully (PRD v1.0, 29 Sep 2026).

**Pulse measures actual venue operations, not just recorded bookings.** Every facility × 30-minute unit has a known
state; every revenue-generating allocation has a financial record; every metric is derived from source records.

## Run it

```bash
./run.sh                 # Mac / Linux → http://localhost:8000
```

```powershell
.\run.ps1                # Windows PowerShell (uses the active Python, e.g. a conda env)
.\run.ps1 -Reset         # rebuild demo data around today
```

Needs Python 3.10+ and Node 20.19+ (or 22.12+).

Demo accounts (password `pulse1234`, all `@rallygully.com`): `admin` (HQ Admin), `hq`, `community`, `academy`, and
Venue Managers `gaurav` (Delhi: Sector 10 & 19 Dwarka, East of Kailash, Panchsheel Park), `govind` (Gurgaon: Sector 83,
DPSG Palam Vihar, Capital Cyberscape, Sec 83 Turf & Padel) and `mohit` (Noida Sector 49, LCR Pune, Baner Pune).

The seed loads the 11 venues and court counts from *List of Venues for Academies.xlsx* (`SHEET` in
`backend/app/seed.py`) with ~100 days of history, so the 7/30/90-day comparisons and anomaly detection have real
baselines. Operating hours (school venues 4–11 PM on weekdays, 7 AM–11 PM weekends; standalone venues 6 AM–11 PM),
prices and demand are **placeholders** — replace them under Configuration. "Today" is built around the day you seed,
so re-run `python -m app.seed --reset` on demo day. Schema changes ship without migrations in V1.

## People, roles and access

People are created only by an **HQ Admin** (People & access), keyed by work email. Each gets a single-use invite link
to set their own password (72 h); "Forgot password?" sends a 1-hour reset link. Links are never written to the
notification log. Without SMTP configured, the admin is shown the link to share directly. Changing a password,
disabling a person or changing their role signs them out everywhere; logins are throttled after 5 failures.

| Role | Can do |
|---|---|
| HQ Admin | Everything, including people and roles |
| HQ | Everything except people management |
| Community Operations | Community Games (create/edit/cancel), customers, full analytics, read-only venue views |
| Academy | Academy bookings and reconciliation, full analytics, read-only venue views |
| Venue Manager | Venue operations — **only at venues and on dates they're on the roster** |

Permissions are defined once in `backend/app/permissions.py` and enforced on every API request; the UI only mirrors
them. **VM roster** (HQ, normally set the day before): each VM belongs to one or more venue teams, and the daily roster
decides who can open which venue that day. Several VMs can share a venue-day. From 8 PM IST, Operations is emailed
about any operating venue with nobody rostered for tomorrow; HQ can add someone on the day and access is immediate.

**Development** (hot reload):

```bash
cd backend && .venv/bin/uvicorn app.main:app --reload     # API on :8000
cd frontend && npm run dev                                 # UI on :5173, proxies /api
cd backend && .venv/bin/pytest                             # acceptance tests (PRD §74)
cd backend && .venv/bin/python -m app.seed --reset         # rebuild demo data
```

## Architecture

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI + SQLAlchemy 2 | Typed, fast to iterate; services hold all business rules, routers stay thin |
| DB | SQLite (WAL) by default, `PULSE_DATABASE_URL` for Postgres | Zero-setup V1; the schema is portable |
| UI | React 19 + Vite + React Router, hand-built design system | Dense ops dashboard; no UI-kit lock-in |
| Jobs | In-process scheduler (`app/scheduler.py`) | District ingestion every 5 min, payment-overdue, 11:30 PM missed-close escalation |

Key ideas:

- **Inventory ledger** (`allocations` table). Every consumed facility-slot — District, Direct, Community, Academy,
  Corporate, Operational Block — is a row. Availability is the gap between active rows inside operating hours; it is
  never stored. Overlaps are rejected inside a lock (`services/inventory.py`). On Postgres, add an exclusion
  constraint on `(facility_id, date, int4range(start_min, end_min))` for defence in depth.
- **District bookings arrive without a court.** Until the VM assigns one on arrival, each holds one court of
  *venue-level* capacity: other allocations are refused if they'd leave fewer free courts than pending District
  arrivals, and the grid shows those cells as "Held for District".
- **Analytics are recomputed from source records** (`services/analytics.py`), memoized per data version. An HQ
  correction to any booking/payment/refund flows into every day, trend, comparison and anomaly automatically.
- **Anomalies** use a robust (median/MAD) z-score against the venue's own same-weekday history (Iglewicz & Hoaglin,
  |z| ≥ 3.5), fall back to the trailing 28 days, and report "insufficient history" rather than guess. Each insight
  states current value, normal range, deviation, correlated movements and the most-deviating facility.

## Venue configuration

HQ (HQ Admin and HQ) create and edit venues (code, name, location, active/inactive), facilities (name, type, sport,
order, active/inactive), weekly hours, special-date overrides and prices under **Configuration**. Edits are refused,
with the affected bookings listed, when they would strand upcoming activity: deactivating a venue or facility,
changing a facility's sport, shrinking hours, or closing a date. Renames are always safe. Prices are versioned —
"Change price" schedules a new price from a date; prices already in use can't be deleted, so past bookings and
historical analytics keep the price they were sold at.

## Integrations

All are real code paths with a safe default when credentials are absent (messages are logged as `simulated` and
visible under **Integrations**):

| Env var | Purpose |
|---|---|
| `PULSE_IMAP_HOST`, `PULSE_IMAP_USER`, `PULSE_IMAP_PASSWORD` | District mailbox. Without it, drop `.eml`/`.txt` files in `backend/inbox/` |
| `PULSE_WHATSAPP_TOKEN`, `PULSE_WHATSAPP_PHONE_ID`, `PULSE_COMMUNITY_LINK` | WhatsApp Cloud API (booking + community link messages) |
| `PULSE_SMTP_HOST`, `PULSE_SMTP_USER`, `PULSE_SMTP_PASSWORD`, `PULSE_OPS_EMAIL` | Missed day-close escalation email |
| `PULSE_SECRET_KEY` | Token signing — **set this in any shared deployment** |
| `PULSE_APP_URL` | Public URL used in invite / reset links |
| `PULSE_ROSTER_ALERT_MIN` | Minute of day (IST) for the "no VM rostered tomorrow" alert; default 1200 (8 PM) |

## PRD decisions and open points

Where the PRD was silent or self-contradictory, this is what V1 does — each is one constant/function to change:

1. **Payment window end.** §19 says a 7–8 PM booking can be paid until 8:30 PM (end + 30); §20.5 says "30 minutes
   after start". Implemented §19 (end + 30) for both channels; VMs can extend.
2. **Revenue Opportunity.** §39 defines it as the value of *unused sellable inventory*, but its example computes
   *potential − actual revenue* (which also counts discounts). Implemented the definition; `potential_value` is
   exposed if HQ prefers the other.
3. **District email format** is undecided (§79). `services/district.py` parses "Label: value" emails with common label
   variants; adjust once real samples exist. Failures are logged per run, never shown to VMs (§16.5).
4. **Academy revenue** is accrued at occurrence value until the month is reconciled; the reconciled amount then
   replaces it, spread across that month's occurrences (§33.2).
5. **Utilization** isn't defined in the PRD. Pulse uses (occupied + blocked) ÷ operational inventory, distinct from
   occupancy.
6. **Live-day anomalies** only check revenue, occupancy and volume; ratio metrics (cash share, repeat share,
   no-shows) wait for the completed day to avoid small-sample noise.
7. "Paddle" in the PRD is modelled as **Padel**, the sport's standard name.
