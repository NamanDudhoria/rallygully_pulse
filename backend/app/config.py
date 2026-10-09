"""Runtime configuration, read once from environment variables."""
import os
from zoneinfo import ZoneInfo

DATABASE_URL = os.getenv("PULSE_DATABASE_URL", "sqlite:///./pulse.db")
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
COMMUNITY_LINK = os.getenv("PULSE_COMMUNITY_LINK", "https://chat.whatsapp.com/rallygully-community")

# Escalation email: SMTP when configured, otherwise logged only.
SMTP_HOST = os.getenv("PULSE_SMTP_HOST")
SMTP_PORT = int(os.getenv("PULSE_SMTP_PORT", "587"))
SMTP_USER = os.getenv("PULSE_SMTP_USER")
SMTP_PASSWORD = os.getenv("PULSE_SMTP_PASSWORD")
OPS_EMAIL = os.getenv("PULSE_OPS_EMAIL", "ops@rallygully.com")

RUN_SCHEDULER = os.getenv("PULSE_RUN_SCHEDULER", "1") == "1"
CORS_ORIGINS = os.getenv("PULSE_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
