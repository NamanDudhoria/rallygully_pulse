"""Outbound WhatsApp and email. Failures are logged; there are no retries (PRD §31-32).

Without provider credentials, messages are recorded with status ``simulated`` so the
full flow is observable in the System screen.
"""
import json
import smtplib
import urllib.request
from email.message import EmailMessage

from sqlalchemy.orm import Session

from .. import config
from ..models import Notification


def _send_whatsapp(phone: str, body: str) -> None:
    payload = json.dumps({
        "messaging_product": "whatsapp", "to": f"91{phone}", "type": "text", "text": {"body": body},
    }).encode()
    req = urllib.request.Request(
        f"https://graph.facebook.com/v20.0/{config.WHATSAPP_PHONE_ID}/messages", data=payload, method="POST",
        headers={"Authorization": f"Bearer {config.WHATSAPP_TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"WhatsApp API HTTP {resp.status}")


def whatsapp(db: Session, phone: str, template: str, body: str, ref_type: str, ref_id: int) -> Notification:
    status, error = "simulated", None
    if config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_ID:
        try:
            _send_whatsapp(phone, body)
            status = "sent"
        except Exception as exc:  # noqa: BLE001 — any provider failure is logged, never raised
            status, error = "failed", str(exc)[:500]
    n = Notification(channel="whatsapp", recipient=phone, template=template, body=body,
                     status=status, error=error, ref_type=ref_type, ref_id=ref_id)
    db.add(n)
    return n


def email(db: Session, to: str, subject: str, body: str, ref_type: str | None = None, ref_id: int | None = None) -> Notification:
    status, error = "simulated", None
    if config.SMTP_HOST:
        try:
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = config.SMTP_USER or "pulse@rallygully.com", to, subject
            msg.set_content(body)
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
                s.starttls()
                if config.SMTP_USER:
                    s.login(config.SMTP_USER, config.SMTP_PASSWORD or "")
                s.send_message(msg)
            status = "sent"
        except Exception as exc:  # noqa: BLE001
            status, error = "failed", str(exc)[:500]
    n = Notification(channel="email", recipient=to, template="day_close_escalation",
                     body=f"{subject}\n\n{body}", status=status, error=error, ref_type=ref_type, ref_id=ref_id)
    db.add(n)
    return n


def booking_message(venue_name: str, when: str, link: str) -> str:
    return (f"Your RallyGully booking at {venue_name} on {when} is confirmed. "
            f"Join the RallyGully community for games and updates: {link}")


def community_message(level: str, link: str) -> str:
    return (f"Thanks for playing with RallyGully! You're set as {level.title()}. "
            f"Join your community group here: {link}")
