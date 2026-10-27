"""Nightly copy of the Pulse database (SQLite), kept for 14 days. Run by cron as the service user.

Uses SQLite's own online-backup call, so the copy is consistent even while the app is writing.
"""
import os
import sqlite3
import sys
import time
from datetime import datetime

KEEP_DAYS = 14
url = os.environ.get("PULSE_DATABASE_URL", "")
if not url.startswith("sqlite:///"):
    print("Not a SQLite database: nothing to back up here (back up a hosted database with pg_dump).")
    sys.exit(0)
src = url.replace("sqlite:///", "", 1)
out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "backups")
os.makedirs(out_dir, exist_ok=True)
dest = os.path.join(out_dir, f"pulse-{datetime.now():%Y%m%d-%H%M}.db")

with sqlite3.connect(src) as a, sqlite3.connect(dest) as b:
    a.backup(b)
check = sqlite3.connect(dest).execute("PRAGMA integrity_check").fetchone()[0]
if check != "ok":
    os.remove(dest)
    print(f"{datetime.now():%F %T} BACKUP FAILED integrity check: {check}")
    sys.exit(1)
os.chmod(dest, 0o640)
cutoff = time.time() - KEEP_DAYS * 86400
for name in os.listdir(out_dir):
    path = os.path.join(out_dir, name)
    if name.startswith("pulse-") and name.endswith(".db") and os.path.getmtime(path) < cutoff:
        os.remove(path)
print(f"{datetime.now():%F %T} backup ok: {dest} ({os.path.getsize(dest) // 1024} KB)")
