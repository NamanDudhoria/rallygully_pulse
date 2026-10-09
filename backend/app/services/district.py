"""District email ingestion (PRD §16).

The exact District email format is an open decision (PRD §79), so parsing is isolated
here behind ``parse_email``. It accepts "Label: value" lines with common label
variants. Unparseable mail is an engineering failure: it is recorded on the
ingestion run (and the file is moved to inbox/failed), never shown to VMs.
"""
import email
import imaplib
import os
import re
import shutil
from datetime import date, datetime
from email import policy

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import config
from ..audit import DomainError
from ..models import IngestionRun, Venue
from ..timeutil import now_local
from . import bookings


class ParseError(ValueError):
    pass


FIELDS = {
    "ref": ("booking id", "booking reference", "reference id", "booking ref", "order id"),
    "venue": ("venue", "venue name", "location"),
    "date": ("date", "booking date", "slot date"),
    "time": ("time", "slot", "slot time", "timing"),
    "amount": ("amount", "total", "amount paid", "booking amount"),
    "payment": ("payment status", "payment", "status"),
    "phone": ("phone", "mobile", "customer phone", "phone number", "contact"),
    "sport": ("sport", "activity"),
}


def _text_of(raw: str | bytes) -> tuple[str, str]:
    msg = email.message_from_bytes(raw if isinstance(raw, bytes) else raw.encode(), policy=policy.default)
    subject = str(msg.get("subject") or "")
    if msg.is_multipart():
        part = msg.get_body(preferencelist=("plain", "html"))
        body = part.get_content() if part else ""
    else:
        body = msg.get_content() if msg.get_content_type().startswith("text") else ""
    if not body.strip() and not msg.keys():
        body = raw.decode() if isinstance(raw, bytes) else raw
    body = re.sub(r"<[^>]+>", "\n", body)
    return subject, body


def _parse_time(value: str) -> int:
    value = value.strip().upper().replace(".", ":")
    for fmt in ("%I:%M %p", "%I %p", "%H:%M", "%I:%M%p", "%I%p"):
        try:
            t = datetime.strptime(value, fmt)
            return t.hour * 60 + t.minute
        except ValueError:
            continue
    raise ParseError(f"Unrecognized time '{value}'")


def _parse_date(value: str) -> date:
    value = re.sub(r"(\d)(st|nd|rd|th)", r"\1", value.strip())
    value = re.sub(r"^[A-Za-z]{3,9},?\s+", "", value)  # drop weekday
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d-%m-%Y", "%b %d %Y", "%B %d %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ParseError(f"Unrecognized date '{value}'")


def parse_email(raw: str | bytes) -> dict:
    subject, body = _text_of(raw)
    found: dict[str, str] = {}
    for line in body.splitlines():
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        label = label.strip().lower().strip("*• ")
        for key, aliases in FIELDS.items():
            if label in aliases and key not in found and value.strip():
                found[key] = value.strip()
    if "ref" not in found:
        m = re.search(r"\b([A-Z]{2,5}-?\d{4,})\b", subject)
        if m:
            found["ref"] = m.group(1)
    missing = [k for k in ("ref", "venue", "date", "time", "amount", "payment", "phone") if k not in found]
    if missing:
        raise ParseError(f"Missing fields: {', '.join(missing)}")
    parts = re.split(r"\s*(?:-|–|to)\s*", found["time"], maxsplit=1, flags=re.I)
    if len(parts) != 2:
        raise ParseError(f"Unrecognized time range '{found['time']}'")
    m = re.search(r"\d[\d,]*(?:\.\d+)?", found["amount"])
    if not m:
        raise ParseError(f"Unrecognized amount '{found['amount']}'")
    amount = float(m.group(0).replace(",", ""))
    pay = found["payment"].lower()
    paid = ("paid" in pay and "unpaid" not in pay and "to be" not in pay) or "prepaid" in pay
    return {
        "external_ref": found["ref"].strip(), "venue": found["venue"], "date": _parse_date(found["date"]),
        "start_min": _parse_time(parts[0]), "end_min": _parse_time(parts[1]), "amount": amount,
        "paid": paid, "phone": found["phone"], "sport": found.get("sport"),
    }


def _venue_for(db: Session, label: str) -> Venue:
    label = label.strip()
    v = db.scalar(select(Venue).where(or_(Venue.code.ilike(label), Venue.name.ilike(label))))
    if not v:
        v = db.scalar(select(Venue).where(Venue.name.ilike(f"%{label}%")))
    if not v:
        raise ParseError(f"Unknown venue '{label}'")
    return v


def ingest_raw(db: Session, raw: str | bytes) -> tuple[str, int | None]:
    """Returns ("created" | "duplicate", booking_id). Raises ParseError/DomainError."""
    data = parse_email(raw)
    venue = _venue_for(db, data["venue"])
    with db.begin_nested():
        b, created = bookings.ingest_district(
            db, external_ref=data["external_ref"], venue_id=venue.id, d=data["date"], start_min=data["start_min"],
            end_min=data["end_min"], amount=data["amount"], paid=data["paid"], phone=data["phone"], sport=data["sport"])
    return ("created" if created else "duplicate"), b.id


def _fetch_imap() -> list[tuple[str, bytes]]:
    out = []
    conn = imaplib.IMAP4_SSL(config.IMAP_HOST)
    conn.login(config.IMAP_USER, config.IMAP_PASSWORD)
    conn.select(config.IMAP_FOLDER)
    _, ids = conn.search(None, "UNSEEN")
    for num in ids[0].split():
        _, data = conn.fetch(num, "(RFC822)")
        out.append((f"imap:{num.decode()}", data[0][1]))
    conn.logout()
    return out


def run_ingestion(db: Session) -> IngestionRun:
    source = "imap" if config.IMAP_HOST else "folder"
    run = IngestionRun(source=source, started_at=now_local(), errors=[])
    db.add(run)
    errors = []
    messages: list[tuple[str, bytes]] = []
    try:
        if source == "imap":
            messages = _fetch_imap()
        else:
            inbox = config.DISTRICT_INBOX_DIR
            os.makedirs(inbox, exist_ok=True)
            for name in sorted(os.listdir(inbox)):
                path = os.path.join(inbox, name)
                if os.path.isfile(path) and name.lower().endswith((".eml", ".txt")):
                    with open(path, "rb") as fh:
                        messages.append((path, fh.read()))
    except Exception as exc:  # noqa: BLE001 — mailbox outage is logged on the run
        errors.append({"message": f"Mailbox error: {exc}"})
    for ident, raw in messages:
        outcome = "failed"
        try:
            result, _ = ingest_raw(db, raw)
            if result == "created":
                run.created += 1
            else:
                run.duplicates += 1
            outcome = "processed"
        except (ParseError, DomainError, ValueError) as exc:
            errors.append({"source": os.path.basename(ident), "message": getattr(exc, "message", str(exc))})
        run.messages += 1
        if source == "folder":
            dest = os.path.join(config.DISTRICT_INBOX_DIR, outcome)
            os.makedirs(dest, exist_ok=True)
            shutil.move(ident, os.path.join(dest, os.path.basename(ident)))
    run.errors = errors
    run.finished_at = now_local()
    db.commit()
    return run


SAMPLE = """From: bookings@district.in
Subject: Booking Confirmed - {ref}

Booking ID: {ref}
Venue: {venue}
Date: {date}
Time: {start} - {end}
Amount: Rs. {amount}
Payment Status: {payment}
Phone: +91 {phone}
"""
