"""Runtime configuration, read once from environment variables."""
import os
from zoneinfo import ZoneInfo

DATABASE_URL = os.getenv("PULSE_DATABASE_URL", "sqlite:///./pulse.db")
# Hosts such as Supabase hand out plain postgresql:// URLs; SQLAlchemy needs to be told which driver to use.
for _plain in ("postgres://", "postgresql://"):
    if DATABASE_URL.startswith(_plain):
        DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len(_plain):]
SECRET_KEY = os.getenv("PULSE_SECRET_KEY", "dev-only-change-me")
TOKEN_TTL_HOURS = int(os.getenv("PULSE_TOKEN_TTL_HOURS", "12"))

# All venues operate in India; every date/time in Pulse is venue-local IST.
TZ = ZoneInfo(os.getenv("PULSE_TZ", "Asia/Kolkata"))

SLOT_MINUTES = 30
PAYMENT_WINDOW_BEFORE_MIN = 30
PAYMENT_WINDOW_AFTER_MIN = 30
NO_SHOW_AFTER_MIN = 30
DAY_CLOSE_DEADLINE_MIN = 23 * 60 + 30  # 11:30 PM
REFUND_WINDOW_DAYS = 14

# District ingestion: IMAP when configured, otherwise a drop folder of .eml/.txt files.
DISTRICT_POLL_SECONDS = int(os.getenv("PULSE_DISTRICT_POLL_SECONDS", "300"))
DISTRICT_INBOX_DIR = os.getenv("PULSE_DISTRICT_INBOX_DIR", "./inbox")
IMAP_HOST = os.getenv("PULSE_IMAP_HOST")
IMAP_USER = os.getenv("PULSE_IMAP_USER")
IMAP_PASSWORD = os.getenv("PULSE_IMAP_PASSWORD")
IMAP_FOLDER = os.getenv("PULSE_IMAP_FOLDER", "INBOX")

# WhatsApp: Meta Cloud API when configured, otherwise messages are logged only.
WHATSAPP_TOKEN = os.getenv("PULSE_WHATSAPP_TOKEN")
WHATSAPP_PHONE_ID = os.getenv("PULSE_WHATSAPP_PHONE_ID")
COMMUNITY_LINK = os.getenv("PULSE_COMMUNITY_LINK", "https://chat.whatsapp.com/rallygully-community")  # venues without their own
# WhatsApp via Twilio (used instead of the Meta settings above when the account SID and token are set).
TWILIO_ACCOUNT_SID = os.getenv("PULSE_TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("PULSE_TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("PULSE_TWILIO_WHATSAPP_FROM")  # e.g. +919876543210, your approved sender
TWILIO_CONTENT_SID_BOOKING = os.getenv("PULSE_TWILIO_CONTENT_SID_BOOKING")  # approved template HX…; vars 1=venue 2=when 3=link
TWILIO_CONTENT_SID_COMMUNITY = os.getenv("PULSE_TWILIO_CONTENT_SID_COMMUNITY")  # optional; vars 1=level 2=link
TWILIO_CONTENT_SID_ROSTER = os.getenv("PULSE_TWILIO_CONTENT_SID_ROSTER")  # VM roster alert template
# Variable names as written in each Twilio template, in the order we fill them (venue, time, link / dates).
TWILIO_BOOKING_VARS = os.getenv("PULSE_TWILIO_BOOKING_VARS", "1,2,3").split(",")
TWILIO_ROSTER_VARS = os.getenv("PULSE_TWILIO_ROSTER_VARS", "venue_name,time-slot").split(",")

# Escalation email: SMTP when configured, otherwise logged only.
SMTP_HOST = os.getenv("PULSE_SMTP_HOST")
SMTP_PORT = int(os.getenv("PULSE_SMTP_PORT", "587"))
SMTP_USER = os.getenv("PULSE_SMTP_USER")
SMTP_PASSWORD = os.getenv("PULSE_SMTP_PASSWORD")
OPS_EMAIL = os.getenv("PULSE_OPS_EMAIL", "ops@rallygully.com")

# Public URL of the app, used in invite / reset links.
APP_URL = os.getenv("PULSE_APP_URL", "http://localhost:8000")
# Evening-before check for venues with no VM rostered for tomorrow (IST minute of day).
ROSTER_ALERT_MIN = int(os.getenv("PULSE_ROSTER_ALERT_MIN", str(20 * 60)))
VM_ALERT_MIN = int(os.getenv("PULSE_VM_ALERT_MIN", str(23 * 60 + 30)))  # each VM is told tomorrow's venue and shift

# Anyone who knows the default key can mint a valid session for any user, so a deployed app must not use it.
if SECRET_KEY == "dev-only-change-me" and not APP_URL.startswith(("http://localhost", "http://127.0.0.1")):
    raise RuntimeError("PULSE_SECRET_KEY must be set to a long random value when PULSE_APP_URL is not localhost "
                       "(generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\")")

# VM attendance: clock-in opens within this distance of the venue, from this long before the shift starts.
ATTENDANCE_RADIUS_M = int(os.getenv("PULSE_ATTENDANCE_RADIUS_M", "500"))
ATTENDANCE_EARLY_MIN = int(os.getenv("PULSE_ATTENDANCE_EARLY_MIN", "60"))
ATTENDANCE_MAX_ACCURACY_M = int(os.getenv("PULSE_ATTENDANCE_MAX_ACCURACY_M", "500"))  # coarser fixes are refused
ATTENDANCE_EMAIL = os.getenv("PULSE_ATTENDANCE_EMAIL") or OPS_EMAIL  # receives each clock-in photo

RUN_SCHEDULER = os.getenv("PULSE_RUN_SCHEDULER", "1") == "1"
CORS_ORIGINS = os.getenv("PULSE_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
