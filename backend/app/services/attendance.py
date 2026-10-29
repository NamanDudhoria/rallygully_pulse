"""VM attendance: clock in on site, photograph the venue, clock out.

A rostered VM's venue stays locked for that day until they clock in within ``ATTENDANCE_RADIUS_M`` of the
venue and send a camera photo of it. The photo is emailed to Operations with the clock-in details and kept
in the database only until the email goes out (retried by the scheduler), so Pulse holds no photo archive.

Location comes from the VM's phone and can be faked with a mock-location app; the server re-checks the
distance and records the reported accuracy, and the photo is the second piece of evidence.
"""
import base64
import binascii
import math
from datetime import date, timedelta

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from .. import audit, config
from ..audit import DomainError
from ..models import AttendancePhoto, Notification, User, Venue, VmAttendance, VmRoster
from ..timeutil import at_minute, minute_of, now_local, tz_label
from . import notify, roster

MAX_PHOTO_BYTES = 3 * 1024 * 1024
RETRY_EVERY = timedelta(minutes=10)


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres (haversine)."""
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def record(db: Session, user_id: int, venue_id: int, d: date) -> VmAttendance | None:
    return db.scalar(select(VmAttendance).where(VmAttendance.user_id == user_id, VmAttendance.venue_id == venue_id,
                                                VmAttendance.date == d))


def unlocked(db: Session, user: User, venue_id: int, d: date) -> bool:
    """The VM has clocked in at this venue on this day and sent the photo."""
    a = record(db, user.id, venue_id, d)
    return bool(a and a.photo_at)


def unlocked_pairs(db: Session, user: User) -> set[tuple[int, date]]:
    return {(a.venue_id, a.date) for a in db.scalars(select(VmAttendance).where(
        VmAttendance.user_id == user.id, VmAttendance.photo_at.is_not(None)))}


def _shift(db: Session, user: User, venue_id: int, d: date) -> tuple[VmRoster | None, tuple[int, int] | None]:
    r = db.scalar(select(VmRoster).where(VmRoster.user_id == user.id, VmRoster.venue_id == venue_id,
                                         VmRoster.date == d))
    return r, (roster.window(db, r) if r else None)


def _opens_closes(w: tuple[int, int]) -> tuple[int, int]:
    """Clock-in window in minutes of the day: from ATTENDANCE_EARLY_MIN before the shift to its end
    (end of day for a shift that runs past midnight)."""
    start, end = w
    return max(0, start - config.ATTENDANCE_EARLY_MIN), (end if end > start else 24 * 60)


def state(db: Session, user: User, venue_id: int, d: date) -> dict:
    """Everything the clock-in screen needs."""
    v = db.get(Venue, venue_id)
    r, w = _shift(db, user, venue_id, d)
    a = record(db, user.id, venue_id, d)
    now = now_local()
    is_today = d == now.date()
    opens = closes = None
    if w:
        opens, closes = _opens_closes(w)
    blocker = None
    if not r:
        blocker = "You're not rostered here on this day."
    elif not is_today:
        blocker = "Clock-in opens only on the day of the shift."
    elif not w:
        blocker = "The venue is closed on this day, so there's no shift to clock in to."
    elif v.latitude is None or v.longitude is None:
        blocker = "HQ hasn't set this venue's map location yet, so clock-in can't check where you are. Ask HQ."
    elif minute_of(now, d) < opens:
        blocker = f"Clock-in opens at {_t12(opens)}."
    elif minute_of(now, d) > closes:
        blocker = "Your shift has ended."
    return {
        "venue": {"id": v.id, "name": v.name, "has_location": v.latitude is not None and v.longitude is not None},
        "date": d, "rostered": r is not None,
        "shift": {"start_min": w[0], "end_min": w[1]} if w else None,
        "opens_min": opens, "radius_m": config.ATTENDANCE_RADIUS_M,
        "clocked_in": a is not None, "unlocked": bool(a and a.photo_at), "clocked_out": bool(a and a.clock_out_at),
        "clock_in_at": a.clock_in_at if a else None, "late_min": a.late_min if a else None,
        "distance_m": round(a.in_distance_m) if a else None, "clock_out_at": a.clock_out_at if a else None,
        "can_clock_in": a is None and blocker is None, "blocker": None if a else blocker,
    }


def _check_fix(lat: float, lng: float, accuracy: float | None) -> None:
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise DomainError("That location isn't valid.", 422)
    if accuracy is not None and accuracy > config.ATTENDANCE_MAX_ACCURACY_M:
        raise DomainError(f"Your phone's location is too rough right now (±{accuracy:.0f} m). Turn on GPS / precise "
                          "location, step outside if you can, and try again.", 422)


def clock_in(db: Session, user: User, venue_id: int, lat: float, lng: float, accuracy: float | None) -> VmAttendance:
    d = now_local().date()
    st = state(db, user, venue_id, d)
    if st["clocked_in"]:
        raise DomainError("You've already clocked in here today.", 409)
    if st["blocker"]:
        raise DomainError(st["blocker"], 409)
    _check_fix(lat, lng, accuracy)
    v = db.get(Venue, venue_id)
    dist = distance_m(lat, lng, v.latitude, v.longitude)
    if dist > config.ATTENDANCE_RADIUS_M:
        raise DomainError(f"You're about {_km(dist)} from {v.name}. Clock-in opens within "
                          f"{config.ATTENDANCE_RADIUS_M} m of the venue.", 409)
    now = now_local()
    start = st["shift"]["start_min"]
    a = VmAttendance(user_id=user.id, venue_id=venue_id, date=d, shift_start_min=start,
                     late_min=max(0, minute_of(now, d) - start), clock_in_at=now, in_lat=lat, in_lng=lng,
                     in_accuracy_m=accuracy, in_distance_m=dist)
    db.add(a)
    db.flush()
    audit.record(db, user, "attendance.clock_in", "attendance", a.id, None,
                 {"distance_m": round(dist), "accuracy_m": accuracy, "late_min": a.late_min}, venue_id)
    return a


def _decode_photo(data_url: str) -> bytes:
    head, _, b64 = data_url.partition(",")
    if not b64 or "base64" not in head:
        raise DomainError("The photo didn't come through. Take it again.", 422)
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise DomainError("The photo didn't come through. Take it again.", 422) from None
    if len(raw) > MAX_PHOTO_BYTES:
        raise DomainError("The photo is too large. Take it again.", 422)
    if not raw.startswith(b"\xff\xd8\xff"):
        raise DomainError("The photo must be taken with the in-app camera.", 422)
    return raw


REQUIRED_PHOTOS = ("venue", "branding", "toilets")
OPTIONAL_PHOTOS = ("other",)
PHOTO_LABELS = {"venue": "Venue", "branding": "Branding", "toilets": "Toilets", "other": "Other"}


def add_photos(db: Session, user: User, venue_id: int, photos: dict[str, str | None]) -> VmAttendance:
    """Store the clock-in photos, unlock the venue, and email Operations straight away. The venue, branding and
    toilets photos are compulsory; "other" is optional. Nothing is kept unless all the compulsory ones are there."""
    a = record(db, user.id, venue_id, now_local().date())
    if not a:
        raise DomainError("Clock in first.", 409)
    if a.photo_at:
        raise DomainError("The photos have already been sent.", 409)
    unknown = [k for k in photos if k not in REQUIRED_PHOTOS + OPTIONAL_PHOTOS]
    if unknown:
        raise DomainError("Unknown photo type.", 422)
    missing = [PHOTO_LABELS[k].lower() for k in REQUIRED_PHOTOS if not photos.get(k)]
    if missing:
        raise DomainError(f"Take all three photos: venue, branding and toilets. Still missing: {', '.join(missing)}.", 422)
    raw = {k: _decode_photo(photos[k]) for k in REQUIRED_PHOTOS + OPTIONAL_PHOTOS if photos.get(k)}
    now = now_local()
    for kind, data in raw.items():
        db.add(AttendancePhoto(attendance_id=a.id, kind=kind, data=data, taken_at=now))
    a.photo_at = now
    audit.record(db, user, "attendance.photo", "attendance", a.id, None,
                 {"photos": list(raw), "bytes": sum(len(x) for x in raw.values())}, venue_id)
    db.flush()
    send_photo(db, a)
    return a


def _photos_of(db: Session, a: VmAttendance) -> list[AttendancePhoto]:
    order = {k: i for i, k in enumerate(REQUIRED_PHOTOS + OPTIONAL_PHOTOS)}
    rows = list(db.scalars(select(AttendancePhoto).where(AttendancePhoto.attendance_id == a.id)))
    return sorted(rows, key=lambda p: order.get(p.kind, 99))


def send_photo(db: Session, a: VmAttendance) -> None:
    """Email the clock-in photos to Operations in one message. Delivered: the photos are deleted; otherwise they
    wait for a retry."""
    u, v = db.get(User, a.user_id), db.get(Venue, a.venue_id)
    when = a.clock_in_at.strftime("%I:%M %p").lstrip("0") + f" {tz_label()}"
    late = f" — {a.late_min} min late" if a.late_min else ""
    rows = [p for p in _photos_of(db, a) if p.data]
    legacy = not rows and a.photo_bytes  # a single photo sent before the three-photo check-in
    subject = f"[Pulse] {u.name} clocked in at {v.name}, {a.date.strftime('%a %d %b')} {when}{late}"
    accuracy = f"±{a.in_accuracy_m:.0f} m" if a.in_accuracy_m is not None else "not reported"
    first = u.name.split()[0].lower()
    attach = [(f"{v.code}-{a.date.isoformat()}-{first}-{p.kind}.jpg", p.data, "image", "jpeg") for p in rows]
    if legacy:
        attach = [(f"{v.code}-{a.date.isoformat()}-{first}.jpg", a.photo_bytes, "image", "jpeg")]
    listing = "\n".join(f"  {i}. {PHOTO_LABELS[p.kind]}" for i, p in enumerate(rows, 1)) if rows else "  1. Venue"
    body = (f"{u.name} clocked in at {v.name}.\n\n"
            f"Date: {a.date.strftime('%A %d %B %Y')}\n"
            f"Clock-in: {when}" + (f" (shift starts {_t12(a.shift_start_min)}{late})" if a.shift_start_min is not None else "") + "\n"
            f"Distance from venue: {a.in_distance_m:.0f} m (phone accuracy {accuracy})\n"
            f"Location: https://maps.google.com/?q={a.in_lat:.6f},{a.in_lng:.6f}\n"
            f"Photos taken: {a.photo_at.strftime('%I:%M %p').lstrip('0')} {tz_label()}\n\n"
            f"Attached ({len(attach)}):\n{listing}\n")
    n = notify.email(db, config.ATTENDANCE_EMAIL, subject, body, ref_type="attendance", ref_id=a.id,
                     template="attendance_photo", attachments=attach)
    a.photo_status, a.photo_error, a.photo_attempted_at = n.status, n.error, now_local()
    if n.status == "sent":
        a.photo_sent_at, a.photo_bytes = a.photo_attempted_at, None
        for p in rows:
            p.data = None


def retry_photos(db: Session) -> int:
    """Scheduler: resend undelivered photos every RETRY_EVERY, once email is configured."""
    if not config.SMTP_HOST:
        return 0
    cutoff = now_local() - RETRY_EVERY
    held = exists().where(AttendancePhoto.attendance_id == VmAttendance.id, AttendancePhoto.data.is_not(None))
    pending = list(db.scalars(select(VmAttendance).where(
        VmAttendance.photo_bytes.is_not(None) | held,
        (VmAttendance.photo_attempted_at.is_(None)) | (VmAttendance.photo_attempted_at <= cutoff))))
    for a in pending:
        send_photo(db, a)
    return len(pending)


def require_shift_over(db: Session, user: User, venue_id: int, d: date, what: str) -> None:
    """Closing out (clock-out, day close) is allowed only once the VM's own shift has ended."""
    _, w = _shift(db, user, venue_id, d)
    if w and minute_of(now_local(), d) < w[1]:
        raise DomainError(f"You can {what} once your shift ends at {_t12(w[1])}.", 409)


def close_out(db: Session, user: User, venue_id: int, d: date) -> VmAttendance | None:
    """Closing the day is the VM's out-attendance: it stamps their clock-out (no location is taken)."""
    a = record(db, user.id, venue_id, d)
    if a and not a.clock_out_at:
        a.clock_out_at = now_local()
        audit.record(db, user, "attendance.clock_out", "attendance", a.id, None, {"via": "day_close"}, venue_id)
    return a


def alert_missing_checkin(db: Session) -> int:
    """When a rostered shift has started and the VM hasn't clocked in, email every active HQ Admin once.
    Entries added after the shift began are skipped: nobody could have checked in for them in time."""
    now = now_local()
    d = now.date()
    now_min = minute_of(now, d)
    admins = [e for e in db.scalars(select(User.email).where(User.role == "hq_admin", User.active.is_(True))
                                    .order_by(User.id))] or [config.OPS_EMAIL]
    sent = 0
    for r, u in list(db.execute(select(VmRoster, User).join(User, User.id == VmRoster.user_id)
                                .where(VmRoster.date == d, User.active.is_(True)))):
        w = roster.window(db, r)
        if not w or not (w[0] <= now_min < w[1]) or r.created_at >= at_minute(d, w[0]):
            continue
        if record(db, u.id, r.venue_id, d) or db.scalar(select(Notification.id).where(
                Notification.template == "attendance_missing", Notification.ref_type == "roster",
                Notification.ref_id == r.id)):
            continue
        v = db.get(Venue, r.venue_id)
        late = now_min - w[0]
        subject = f"[Pulse] {u.name} has not checked in at {v.name} (shift started {_t12(w[0])} {tz_label()})"
        body = (f"{u.name} was rostered at {v.name} today, {d.strftime('%A %d %B %Y')}, from {_t12(w[0])} to "
                f"{_t12(w[1])} {tz_label()}.\n\nThey had not clocked in {late} minute{'s' if late != 1 else ''} after "
                f"the shift started.\nPhone: {u.phone or 'not on file'}\n\nThe venue stays locked for them until they "
                f"clock in and send the venue photo.")
        for to in admins:
            notify.email(db, to, subject, body, "roster", r.id, template="attendance_missing")
        sent += 1
    return sent


def clock_out(db: Session, user: User, venue_id: int, lat: float, lng: float, accuracy: float | None) -> VmAttendance:
    """Clock-out records where it happened. Allowed only after the shift ends; being away from the venue is
    recorded, not refused."""
    a = record(db, user.id, venue_id, now_local().date())
    if not a:
        raise DomainError("You haven't clocked in here today.", 409)
    if a.clock_out_at:
        raise DomainError("You've already clocked out.", 409)
    require_shift_over(db, user, venue_id, a.date, "clock out")
    _check_fix(lat, lng, accuracy)
    v = db.get(Venue, venue_id)
    a.clock_out_at, a.out_lat, a.out_lng, a.out_accuracy_m = now_local(), lat, lng, accuracy
    a.out_distance_m = distance_m(lat, lng, v.latitude, v.longitude)
    audit.record(db, user, "attendance.clock_out", "attendance", a.id, None,
                 {"distance_m": round(a.out_distance_m), "accuracy_m": accuracy}, venue_id)
    return a


def summary(a: VmAttendance | None) -> dict | None:
    """What HQ sees on the roster and the venue page."""
    if not a:
        return None
    return {"clock_in_at": a.clock_in_at, "late_min": a.late_min, "distance_m": round(a.in_distance_m),
            "photo": a.photo_status if a.photo_at else "missing", "clock_out_at": a.clock_out_at,
            "out_distance_m": round(a.out_distance_m) if a.out_distance_m is not None else None,
            "left_away": a.out_distance_m is not None and a.out_distance_m > config.ATTENDANCE_RADIUS_M}


def by_roster(db: Session, pairs: list[tuple[int, int, date]]) -> dict:
    """{(user_id, venue_id, date): summary} for a set of roster entries, in one query."""
    if not pairs:
        return {}
    users = {p[0] for p in pairs}
    days = {p[2] for p in pairs}
    rows = db.scalars(select(VmAttendance).where(VmAttendance.user_id.in_(users), VmAttendance.date.in_(days)))
    return {(a.user_id, a.venue_id, a.date): summary(a) for a in rows}


def _t12(minute: int) -> str:
    """Minutes after midnight → "3:00 PM" (how VMs and ops read times)."""
    h, m = divmod(minute % (24 * 60), 60)
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def _km(m: float) -> str:
    return f"{m / 1000:.1f} km" if m >= 1000 else f"{m:.0f} m"
