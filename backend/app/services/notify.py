"""Outbound WhatsApp and email. Failures are logged; there are no retries (PRD §31-32).

Without provider credentials, messages are recorded with status ``simulated`` so the
full flow is observable in the System screen.
"""
import base64
import json
import smtplib
import urllib.error
import urllib.parse
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


def _send_twilio(phone: str, body: str, content_sid: str | None, variables: list[str] | None) -> None:
    """Approved template when ``content_sid`` is set (needed to message customers first), else plain text."""
    data = {"From": f"whatsapp:{config.TWILIO_WHATSAPP_FROM}", "To": f"whatsapp:+91{phone}"}
    if content_sid and variables:
        data["ContentSid"] = content_sid
        data["ContentVariables"] = json.dumps({str(i): v for i, v in enumerate(variables, 1)})
    else:
        data["Body"] = body
    sid = config.TWILIO_ACCOUNT_SID
    req = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", method="POST",
        data=urllib.parse.urlencode(data).encode(),
        headers={"Authorization": "Basic " + base64.b64encode(f"{sid}:{config.TWILIO_AUTH_TOKEN}".encode()).decode(),
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"Twilio HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise RuntimeError(f"Twilio HTTP {exc.code}: {detail}") from exc


def whatsapp(db: Session, phone: str, template: str, body: str, ref_type: str, ref_id: int,
             content_sid: str | None = None, variables: list[str] | None = None) -> Notification:
    status, error = "simulated", None
    twilio = config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN and config.TWILIO_WHATSAPP_FROM
    if twilio or (config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_ID):
        try:
            if twilio:
                _send_twilio(phone, body, content_sid, variables)
            else:
                _send_whatsapp(phone, body)
            status = "sent"
        except Exception as exc:  # noqa: BLE001 — any provider failure is logged, never raised
            status, error = "failed", str(exc)[:500]
    n = Notification(channel="whatsapp", recipient=phone, template=template, body=body,
                     status=status, error=error, ref_type=ref_type, ref_id=ref_id)
    db.add(n)
    return n


def email(db: Session, to: str, subject: str, body: str, ref_type: str | None = None, ref_id: int | None = None,
          template: str = "day_close_escalation", log_body: str | None = None,
          attachments: list[tuple[str, bytes, str, str]] | None = None) -> Notification:
    """Send (or simulate) an email. ``log_body`` replaces the stored copy, e.g. to keep secrets out of logs.
    ``attachments``: (filename, data, maintype, subtype); they are sent but never stored in the log."""
    status, error = "simulated", None
    if config.SMTP_HOST:
        try:
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = config.SMTP_USER or "pulse@rallygully.com", to, subject
            msg.set_content(body)
            for filename, data, maintype, subtype in attachments or []:
                msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
                s.starttls()
                if config.SMTP_USER:
                    s.login(config.SMTP_USER, config.SMTP_PASSWORD or "")
                s.send_message(msg)
            status = "sent"
        except Exception as exc:  # noqa: BLE001
            status, error = "failed", str(exc)[:500]
    n = Notification(channel="email", recipient=to, template=template,
                     body=f"{subject}\n\n{log_body if log_body is not None else body}", status=status, error=error, ref_type=ref_type, ref_id=ref_id)
    db.add(n)
    return n


def booking_message(venue_name: str, when: str, link: str) -> str:
    return (f"Your RallyGully booking at {venue_name} on {when} is confirmed. "
            f"Join the RallyGully community for games and updates: {link}")


def booking_email(venue_name: str, when: str, link: str) -> tuple[str, str]:
    return (f"Your RallyGully booking at {venue_name} is confirmed",
            f"Hi,\n\nYour booking at {venue_name} on {when} is confirmed.\n\n"
            f"Join the RallyGully community for games and updates: {link}\n\nSee you on court!\nRallyGully")


def community_message(level: str, link: str) -> str:
    return (f"Thanks for playing with RallyGully! You're set as {level.title()}. "
            f"Join your community group here: {link}")
