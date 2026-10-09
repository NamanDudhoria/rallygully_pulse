"""Pulse data model.

Times are stored as minutes from venue-local midnight (``start_min``/``end_min``),
always on 30-minute boundaries. The ``Allocation`` table is the inventory ledger:
every facility×time that is consumed (by any activity or block) has an active row,
and availability is derived from it — never stored separately.
"""
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(10))  # hq | vm
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    venues: Mapped[list["VmAssignment"]] = relationship(cascade="all, delete-orphan")


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
    facilities: Mapped[list["Facility"]] = relationship(back_populates="venue", order_by="Facility.sort_order")
    weekly_hours: Mapped[list["WeeklyHours"]] = relationship(cascade="all, delete-orphan", order_by="WeeklyHours.weekday")


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)


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
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    level: Mapped[str] = mapped_column(String(14))  # beginner | intermediate | advanced
    attendance: Mapped[str] = mapped_column(String(10), default="added")  # added | attended | no_show
    payment_status: Mapped[str] = mapped_column(String(10), default="unpaid")  # unpaid | paid
    amount: Mapped[float] = mapped_column(Float, default=0)
    payment_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    game: Mapped[CommunityGame] = relationship(back_populates="participants")
    customer: Mapped[Customer] = relationship()
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
