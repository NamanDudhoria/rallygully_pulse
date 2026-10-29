"""Pulse data model.

Times are stored as minutes from venue-local midnight (``start_min``/``end_min``),
always on 30-minute boundaries. The ``Allocation`` table is the inventory ledger:
every facility×time that is consumed (by any activity or block) has an active row,
and availability is derived from it — never stored separately.
"""
from datetime import date, datetime

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .timeutil import now_local


class AllocationKind:
    DISTRICT = "district"
    DIRECT = "direct"
    COMMUNITY = "community"
    ACADEMY = "academy"
    CORPORATE = "corporate"
    BLOCK = "block"
    ALL = (DISTRICT, DIRECT, COMMUNITY, ACADEMY, CORPORATE, BLOCK)
    REVENUE = (DISTRICT, DIRECT, COMMUNITY, ACADEMY, CORPORATE)


class User(Base):
    """A person, identified by work email. Created only by HQ (no self-signup)."""
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(300), nullable=True)  # None until invite accepted
    role: Mapped[str] = mapped_column(String(12))  # see permissions.ROLES
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)  # 10-digit mobile; WhatsApp roster alerts
    # Bumped on password change / deactivation: every previously issued session stops working.
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # VM venue team: the venues a VM may be rostered at.
    venues: Mapped[list["VmAssignment"]] = relationship(cascade="all, delete-orphan")


class AuthToken(Base):
    """Single-use invite / password-reset token. Only a SHA-256 hash is stored."""
    __tablename__ = "auth_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    purpose: Mapped[str] = mapped_column(String(10))  # invite | reset
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)


class VmRoster(Base):
    """Who runs which venue on which date. Set by HQ, normally the day before; gates VM access."""
    __tablename__ = "vm_roster"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    date: Mapped[date] = mapped_column(Date)
    # Shift window in minutes from midnight; both null = the venue's full operating hours.
    start_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    __table_args__ = (UniqueConstraint("venue_id", "user_id", "date"), Index("ix_roster_date", "date"))


class VmAttendance(Base):
    """A VM's clock-in / clock-out at a rostered venue. The venue opens to them only after clock-in on site
    and a camera photo of the venue; the photo is emailed to Operations and kept here only until delivered."""
    __tablename__ = "vm_attendance"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    date: Mapped[date] = mapped_column(Date)
    shift_start_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    late_min: Mapped[int] = mapped_column(Integer, default=0)
    clock_in_at: Mapped[datetime] = mapped_column(DateTime)
    in_lat: Mapped[float] = mapped_column(Float)
    in_lng: Mapped[float] = mapped_column(Float)
    in_accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    in_distance_m: Mapped[float] = mapped_column(Float)
    photo_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # set = venue unlocked
    photo_status: Mapped[str | None] = mapped_column(String(12), nullable=True)  # sent | simulated | failed
    photo_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # only until emailed
    photo_attempted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    photo_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    photo_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    clock_out_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    out_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    out_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    out_accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    out_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    __table_args__ = (UniqueConstraint("user_id", "venue_id", "date"), Index("ix_attendance_date", "date"))


class RosterAlert(Base):
    """Records that Operations was warned about an unrostered venue-day (sent once)."""
    __tablename__ = "roster_alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    date: Mapped[date] = mapped_column(Date)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    __table_args__ = (UniqueConstraint("venue_id", "date"),)


class VmAssignment(Base):
    __tablename__ = "vm_assignments"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), primary_key=True)


class Venue(Base):
    __tablename__ = "venues"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    location: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    # Map position, for VM clock-in: attendance opens only within the radius of this point.
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    community_link: Mapped[str | None] = mapped_column(String(300), nullable=True)  # WhatsApp group for this venue
    facilities: Mapped[list["Facility"]] = relationship(back_populates="venue", order_by="Facility.sort_order")
    weekly_hours: Mapped[list["WeeklyHours"]] = relationship(cascade="all, delete-orphan", order_by="WeeklyHours.weekday")
    discounts: Mapped[list["Discount"]] = relationship(order_by="Discount.id")
    rental_items: Mapped[list["RentalItem"]] = relationship(order_by="RentalItem.id")


class Discount(Base):
    """A percentage off the configured price that a booking at this venue may use. Retired, never deleted,
    so bookings that used it keep their record."""
    __tablename__ = "discounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(60))
    percent: Mapped[float] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)


class RentalItem(Base):
    """Equipment a venue rents out, at a fixed price. Retired, never deleted, so past charges keep their record."""
    __tablename__ = "rental_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(60))
    price: Mapped[float] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)


class Facility(Base):
    __tablename__ = "facilities"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(80))
    facility_type: Mapped[str] = mapped_column(String(40))  # Court, Turf, ...
    sport: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="active")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    venue: Mapped[Venue] = relationship(back_populates="facilities")


class WeeklyHours(Base):
    __tablename__ = "weekly_hours"
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), primary_key=True)
    weekday: Mapped[int] = mapped_column(Integer, primary_key=True)  # 0 = Monday
    open_min: Mapped[int] = mapped_column(Integer, default=0)
    close_min: Mapped[int] = mapped_column(Integer, default=0)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)


class SpecialDate(Base):
    __tablename__ = "special_dates"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    date: Mapped[date] = mapped_column(Date)
    open_min: Mapped[int] = mapped_column(Integer, default=0)
    close_min: Mapped[int] = mapped_column(Integer, default=0)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(200), default="")
    __table_args__ = (UniqueConstraint("venue_id", "date"),)


class PricingRule(Base):
    """Price for one booking of ``duration_min`` starting inside [start_min, end_min).

    Specificity: facility > sport > venue-wide. Among equally specific rules the
    latest ``effective_from`` not after the booking's creation date wins, so price
    changes only ever affect bookings created afterwards.
    """
    __tablename__ = "pricing_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    facility_id: Mapped[int | None] = mapped_column(ForeignKey("facilities.id"), nullable=True)
    sport: Mapped[str | None] = mapped_column(String(40), nullable=True)
    weekdays: Mapped[str] = mapped_column(String(7), default="0123456")
    start_min: Mapped[int] = mapped_column(Integer)
    end_min: Mapped[int] = mapped_column(Integer)
    duration_min: Mapped[int] = mapped_column(Integer, default=60)
    amount: Mapped[float] = mapped_column(Float)
    effective_from: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    name_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)  # optional; booking confirmations
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)


class RentalCharge(Base):
    """Equipment rented against a booking. Its own line and its own payment: kept out of court revenue."""
    __tablename__ = "rental_charges"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"))
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    item_name: Mapped[str] = mapped_column(String(60))
    unit_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer)
    amount: Mapped[float] = mapped_column(Float)
    paid_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    __table_args__ = (Index("ix_rental_booking", "booking_id"),)


class Allocation(Base):
    __tablename__ = "allocations"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    facility_id: Mapped[int] = mapped_column(ForeignKey("facilities.id"))
    date: Mapped[date] = mapped_column(Date)
    start_min: Mapped[int] = mapped_column(Integer)
    end_min: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    ref_id: Mapped[int] = mapped_column(Integer)  # booking / game / occurrence / event / block id
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (
        Index("ix_alloc_facility_date", "facility_id", "date", "active"),
        Index("ix_alloc_ref", "kind", "ref_id"),
        Index("ix_alloc_venue_date", "venue_id", "date"),
    )


class Booking(Base):
    """District or Direct consumer booking (30 or 60 minutes)."""
    __tablename__ = "bookings"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(10))  # district | direct
    external_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    facility_id: Mapped[int | None] = mapped_column(ForeignKey("facilities.id"), nullable=True)
    original_facility_id: Mapped[int | None] = mapped_column(ForeignKey("facilities.id"), nullable=True)
    sport: Mapped[str | None] = mapped_column(String(40), nullable=True)
    date: Mapped[date] = mapped_column(Date)
    start_min: Mapped[int] = mapped_column(Integer)
    end_min: Mapped[int] = mapped_column(Integer)
    booking_value: Mapped[float] = mapped_column(Float)
    list_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # configured price before any discount
    discount_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    discount_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    platform: Mapped[str | None] = mapped_column(String(30), nullable=True)  # online platform other than District
    payment_status: Mapped[str] = mapped_column(String(20))  # paid | to_collect | overdue
    payment_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    collected_amount: Mapped[float] = mapped_column(Float, default=0)
    payment_window_end_min: Mapped[int] = mapped_column(Integer)  # may exceed 1440
    attendance: Mapped[str] = mapped_column(String(10), default="pending")  # pending | attended | no_show
    status: Mapped[str] = mapped_column(String(12), default="confirmed")  # confirmed | cancelled
    cancel_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cancel_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    customer: Mapped[Customer] = relationship()
    __table_args__ = (
        UniqueConstraint("source", "external_ref", name="uq_booking_source_ref"),
        Index("ix_booking_venue_date", "venue_id", "date"),
    )


class Refund(Base):
    __tablename__ = "refunds"
    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"))
    amount: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(12), default="processed")  # pending | processed
    processed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)


class CommunityGame(Base):
    __tablename__ = "community_games"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    title: Mapped[str] = mapped_column(String(120), default="Community Game")
    sport: Mapped[str] = mapped_column(String(40))
    date: Mapped[date] = mapped_column(Date)
    start_min: Mapped[int] = mapped_column(Integer)
    end_min: Mapped[int] = mapped_column(Integer)
    capacity: Mapped[int] = mapped_column(Integer)
    per_person: Mapped[float] = mapped_column(Float)
    community_link: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(12), default="scheduled")  # scheduled | cancelled
    cancel_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cancel_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    participants: Mapped[list["Participant"]] = relationship(back_populates="game", cascade="all, delete-orphan")


class Participant(Base):
    __tablename__ = "participants"
    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("community_games.id"))
    # Players listed by name only (e.g. from the WhatsApp sign-up post) have no customer until a phone is added.
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    guest_of: Mapped[int | None] = mapped_column(ForeignKey("participants.id"), nullable=True)  # "Jatan + 1"
    level: Mapped[str] = mapped_column(String(14))  # beginner | intermediate | advanced
    attendance: Mapped[str] = mapped_column(String(10), default="added")  # added | attended | no_show
    payment_status: Mapped[str] = mapped_column(String(10), default="unpaid")  # unpaid | paid
    amount: Mapped[float] = mapped_column(Float, default=0)
    payment_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    game: Mapped[CommunityGame] = relationship(back_populates="participants")
    customer: Mapped[Customer | None] = relationship()
    __table_args__ = (UniqueConstraint("game_id", "customer_id"),)


class AcademySeries(Base):
    """An Academy arrangement. One-time bookings are a series with one occurrence."""
    __tablename__ = "academy_series"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    name: Mapped[str] = mapped_column(String(120))
    facility_ids: Mapped[list] = mapped_column(JSON)
    recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    weekdays: Mapped[str] = mapped_column(String(7), default="")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    start_min: Mapped[int] = mapped_column(Integer)
    end_min: Mapped[int] = mapped_column(Integer)
    value_per_occurrence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(12), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    occurrences: Mapped[list["AcademyOccurrence"]] = relationship(back_populates="series", order_by="AcademyOccurrence.date")


class AcademyOccurrence(Base):
    __tablename__ = "academy_occurrences"
    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("academy_series.id"))
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    date: Mapped[date] = mapped_column(Date)
    start_min: Mapped[int] = mapped_column(Integer)
    end_min: Mapped[int] = mapped_column(Integer)
    value: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(12), default="scheduled")  # scheduled | cancelled | skipped
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    series: Mapped[AcademySeries] = relationship(back_populates="occurrences")


class AcademyReconciliation(Base):
    """Month-end collected amount for a recurring Academy series."""
    __tablename__ = "academy_reconciliations"
    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("academy_series.id"))
    month: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    amount_collected: Mapped[float] = mapped_column(Float)
    payment_method: Mapped[str] = mapped_column(String(10))
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    __table_args__ = (UniqueConstraint("series_id", "month"),)


class CorporateEvent(Base):
    __tablename__ = "corporate_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    company: Mapped[str] = mapped_column(String(160))
    contact_name: Mapped[str] = mapped_column(String(120))
    contact_phone: Mapped[str] = mapped_column(String(20))
    date: Mapped[date] = mapped_column(Date)
    start_min: Mapped[int] = mapped_column(Integer)
    end_min: Mapped[int] = mapped_column(Integer)
    amount: Mapped[float] = mapped_column(Float)
    payment_status: Mapped[str] = mapped_column(String(12), default="to_collect")  # paid | to_collect
    payment_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="confirmed")
    cancel_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)


class OperationalBlock(Base):
    __tablename__ = "operational_blocks"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    facility_id: Mapped[int] = mapped_column(ForeignKey("facilities.id"))
    date: Mapped[date] = mapped_column(Date)
    start_min: Mapped[int] = mapped_column(Integer)
    end_min: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(30))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(14))  # booking | participant | event | academy
    source_id: Mapped[int] = mapped_column(Integer)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    activity_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(10))
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    void: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (Index("ix_payment_venue_date", "venue_id", "activity_date"),)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_name: Mapped[str] = mapped_column(String(120))
    venue_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entity: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=now_local)


class DayClose(Base):
    __tablename__ = "day_closes"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10))  # closed | missed
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    missing_items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (UniqueConstraint("venue_id", "date"),)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(10))  # whatsapp | email
    recipient: Mapped[str] = mapped_column(String(200))
    template: Mapped[str] = mapped_column(String(40))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12))  # sent | simulated | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ref_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    messages: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list | None] = mapped_column(JSON, nullable=True)


# ── Historical ledger (pre-Pulse sales workbooks) ────────────────────────────
# Imported from the monthly venue sales files. These are financial records (monthly P&L, daily
# offline takings, platform settlements) — not slot bookings — so they live apart from the
# allocation ledger and never affect inventory.
class HistSource(Base):
    """One source workbook (or tracker tab) and how it was used."""
    __tablename__ = "hist_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    file: Mapped[str] = mapped_column(Text)
    sheet: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(Text)
    venue_id: Mapped[int | None] = mapped_column(ForeignKey("venues.id"), nullable=True)
    venue_label: Mapped[str] = mapped_column(Text)  # name as written in the source
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text)  # ingested | duplicate | excluded
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    stated_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    ingested_total: Mapped[float | None] = mapped_column(Float, nullable=True)


class HistRevenue(Base):
    """A revenue line. ``year``/``month`` is the accounting month of the source; dates say when it happened."""
    __tablename__ = "hist_revenue"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("hist_sources.id"))
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    basis: Mapped[str] = mapped_column(Text)  # day | period | month
    vertical: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text)
    sport: Mapped[str | None] = mapped_column(Text, nullable=True)
    gross: Mapped[float] = mapped_column(Float)
    commission: Mapped[float | None] = mapped_column(Float, nullable=True)  # incl. GST on commission
    gateway: Mapped[float | None] = mapped_column(Float, nullable=True)
    other_costs: Mapped[float | None] = mapped_column(Float, nullable=True)  # event expenses, brand payouts
    tds: Mapped[float | None] = mapped_column(Float, nullable=True)
    tcs: Mapped[float | None] = mapped_column(Float, nullable=True)
    net: Mapped[float | None] = mapped_column(Float, nullable=True)  # realised, as stated in the source
    hours: Mapped[float | None] = mapped_column(Float, nullable=True)  # offline booking hours (daily sheets)
    people: Mapped[int | None] = mapped_column(Integer, nullable=True)
    counterparty: Mapped[str | None] = mapped_column(Text, nullable=True)  # member, brand, organisation
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sheet: Mapped[str] = mapped_column(Text)
    row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (Index("ix_hist_rev_venue_period", "venue_id", "year", "month"),)


class HistPnl(Base):
    """Below-the-line items from each monthly Summary: GST, commissions, expenses, profit shares."""
    __tablename__ = "hist_pnl"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("hist_sources.id"))
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"))
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(Text)  # gst | commissions | net | expense | expenses_total | profit | share | other
    label: Mapped[str] = mapped_column(Text)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class HistBooking(Base):
    """A booking from a platform export (Hudle, Playo, Khelomore, District): one court, one time range."""
    __tablename__ = "hist_bookings"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    start_min: Mapped[int] = mapped_column(Integer)
    end_min: Mapped[int] = mapped_column(Integer)
    sport: Mapped[str] = mapped_column(Text)
    court: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(Text)
    medium: Mapped[str] = mapped_column(Text)  # online | offline (entered by the venue, paid there)
    amount: Mapped[float] = mapped_column(Float)  # money the export shows for this booking
    revenue: Mapped[float] = mapped_column(Float)  # the part that is this channel's revenue (no wallet credit)
    customer: Mapped[str] = mapped_column(Text)
    customer_key: Mapped[str] = mapped_column(Text, index=True)  # venue + normalised name
    ref: Mapped[str] = mapped_column(Text)
    file: Mapped[str] = mapped_column(Text)
    sheet: Mapped[str] = mapped_column(Text)
    row: Mapped[int] = mapped_column(Integer)


class HistSlot(Base):
    """Court time per venue, day, hour and sport: capacity, recorded bookings and imputed offline play."""
    __tablename__ = "hist_slots"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    hour: Mapped[int] = mapped_column(Integer)
    sport: Mapped[str] = mapped_column(Text)
    capacity_min: Mapped[float] = mapped_column(Float)
    recorded_min: Mapped[float] = mapped_column(Float)
    imputed_min: Mapped[float] = mapped_column(Float)


class HistBookingMonth(Base):
    """Per venue-month audit of the bookings import: platform totals vs the sales file, and the imputation."""
    __tablename__ = "hist_booking_months"
    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)


class HistTracker(Base):
    """The B2C trackers' Central Dashboard figures, captured at import so Pulse can be reconciled against them."""
    __tablename__ = "hist_tracker"
    id: Mapped[int] = mapped_column(primary_key=True)
    file: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)  # dashboard row, e.g. "RallyGully I Gandhipath"
    venue_id: Mapped[int | None] = mapped_column(ForeignKey("venues.id"), nullable=True)  # None: not a Pulse venue
    kind: Mapped[str] = mapped_column(Text)  # venue | total
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    amount: Mapped[float] = mapped_column(Float)


class HistIssue(Base):
    """Everything the import noticed: mismatches, fallbacks, exclusions — kept for audit."""
    __tablename__ = "hist_issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("hist_sources.id"), nullable=True)
    venue_id: Mapped[int | None] = mapped_column(ForeignKey("venues.id"), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    severity: Mapped[str] = mapped_column(Text)  # info | warn | error
    code: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
