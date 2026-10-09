"""Portfolio dashboard, drill-down (portfolio → venue → sport → facility), trends and insights."""
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user, require_hq, venue_ids_for
from ..db import get_db
from ..models import Booking, DayClose, Facility, User
from ..services import analytics, anomaly, bookings, dayclose, inventory
from ..timeutil import hhmm, now_local, today
from .common import day_or_today
from .venues import DAY_METRICS, intraday_curve

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _slim(m: dict) -> dict:
    keys = ("net_revenue", "collected", "booking_value", "refunds", "outstanding", "opportunity", "potential_value",
            "occupancy", "utilization", "occupied_units", "sellable_units", "operational_units", "blocked_units",
            "unused_units", "reserved_units", "total_activity", "unique_customers", "returning_customers",
            "no_shows", "cancellations", "overdue", "unassigned_district", "cash_share", "activities",
            "net_by_kind", "occupied_by_kind", "collected_by_method", "academy_accrued")
    return {k: m.get(k) for k in keys}


@router.get("/portfolio")
def portfolio(date: date | None = None, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    d = day_or_today(date)
    bookings.refresh_overdue(db)
    db.commit()
    venues = analytics.venues_list(db, None)
    ids = [v.id for v in venues]
    is_today = d == today()
    cutoff = analytics.now_cutoff(d)
    total = analytics.daily_series(db, ids, d, d).get(d)
    per_venue = analytics.raw_days(db, ids, d, d)
    rows = []
    for v in venues:
        m = analytics.finalize(analytics.combine([per_venue[(v.id, d)]] if (v.id, d) in per_venue else []))
        cmp_ = analytics.comparisons(db, [v.id], d, ["net_revenue", "occupancy", "total_activity"],
                                     cutoff if is_today else None)
        rows.append({"venue": {"id": v.id, "name": v.name, "code": v.code, "location": v.location},
                     "hours": _hours(db, v.id, d), "metrics": _slim(m), "comparisons": cmp_,
                     "day_close": dayclose.status(db, v.id, d)["status"]})
    trend = analytics.daily_series(db, ids, d - timedelta(days=89), d)
    insights = anomaly.portfolio(db, venues, d, cutoff) if total else []
    if is_today:
        insights += anomaly.portfolio(db, venues, d - timedelta(days=1))
    hours = [h for h in (inventory.hours_for(db, v.id, d) for v in venues) if h]
    span = (min(h[0] for h in hours), max(h[1] for h in hours)) if hours else None
    return {
        "date": d, "is_today": is_today, "comparison_mode": "intraday" if is_today else "full_day",
        "cutoff_label": hhmm(cutoff) if cutoff is not None else None,
        "metrics": _slim(total) if total else None,
        "comparisons": analytics.comparisons(db, ids, d, DAY_METRICS, cutoff if is_today else None),
        "repeat": analytics.repeat_rate(db, d, d - timedelta(days=89)),
        "repeat_lifetime": analytics.repeat_rate(db, d),
        "venues": rows,
        "trend": [{"date": k, "net_revenue": m["net_revenue"], "occupancy": m["occupancy"],
                   "opportunity": m["opportunity"], "total_activity": m["total_activity"]} for k, m in trend.items()],
        "intraday": intraday_curve(db, ids, d, span) if is_today else None,
        "insights": insights,
        "exceptions": _exceptions(db, ids, d),
        "history_days": analytics.history_days(db),
    }


def _hours(db, venue_id, d):
    h = inventory.hours_for(db, venue_id, d)
    return {"open_min": h[0], "close_min": h[1]} if h else None


def _exceptions(db: Session, ids: list[int], d: date) -> list[dict]:
    out = []
    for b in db.scalars(select(Booking).where(Booking.venue_id.in_(ids), Booking.status == "confirmed",
                                              Booking.payment_status == "overdue", Booking.date >= d - timedelta(days=7),
                                              Booking.date <= d)):
        out.append({"type": "payment_overdue", "venue_id": b.venue_id, "date": b.date, "ref_id": b.id,
                    "message": f"{b.source.title()} booking {hhmm(b.start_min)} — payment overdue (₹{b.booking_value:,.0f})"})
    now_min = (now_local() - now_local().replace(hour=0, minute=0, second=0, microsecond=0)).seconds // 60
    for b in db.scalars(select(Booking).where(Booking.venue_id.in_(ids), Booking.date == d, Booking.source == "district",
                                              Booking.status == "confirmed", Booking.facility_id.is_(None),
                                              Booking.attendance == "pending")):
        if d < today() or b.start_min + 30 <= now_min:
            out.append({"type": "district_unresolved", "venue_id": b.venue_id, "date": b.date, "ref_id": b.id,
                        "message": f"District {b.external_ref} at {hhmm(b.start_min)} — no court / arrival recorded"})
    for dc in db.scalars(select(DayClose).where(DayClose.venue_id.in_(ids), DayClose.status == "missed",
                                                DayClose.date >= d - timedelta(days=7))):
        out.append({"type": "missed_close", "venue_id": dc.venue_id, "date": dc.date, "ref_id": dc.id,
                    "message": f"Day close missed — {len(dc.missing_items or [])} item(s) outstanding"})
    return out


@router.get("/drill")
def drill(date_from: date, date_to: date, venue_id: int | None = None, sport: str | None = None,
          user: User = Depends(current_user), db: Session = Depends(get_db)):
    """One level of the drill-down with the matching previous period for comparison."""
    allowed = venue_ids_for(user)
    if date_to < date_from or (date_to - date_from).days > 366:
        raise HTTPException(422, "Choose a range of up to one year.")
    span = (date_to - date_from).days + 1
    prev_from, prev_to = date_from - timedelta(days=span), date_from - timedelta(days=1)
    venues = analytics.venues_list(db, allowed)
    if venue_id is not None:
        venues = [v for v in venues if v.id == venue_id]
        if not venues:
            raise HTTPException(403, "No access to this venue")
    ids = [v.id for v in venues]

    def level_rows(start, end):
        raw = analytics.compute(db, ids, start, end, with_facilities=venue_id is not None)
        groups: dict = defaultdict(list)
        if venue_id is None:
            for (vid, _), m in raw["days"].items():
                groups[vid].append(m)
        else:
            facs = {f.id: f for f in db.scalars(select(Facility).where(Facility.venue_id == venue_id))}
            for (fid, _), m in raw["facilities"].items():
                f = facs.get(fid)
                if not f:
                    continue
                if sport is None:
                    groups[f.sport].append(m)
                elif f.sport == sport:
                    groups[fid].append(m)
        return {k: analytics.finalize(analytics.combine(v)) for k, v in groups.items()}

    cur, prev = level_rows(date_from, date_to), level_rows(prev_from, prev_to)
    first = analytics.first_data_date(db)
    prev_available = bool(first and first <= prev_from)
    names = {}
    if venue_id is None:
        level = "venue"
        names = {v.id: v.name for v in venues}
    elif sport is None:
        level = "sport"
        names = {k: k for k in cur}
    else:
        level = "facility"
        names = {f.id: f.name for f in db.scalars(select(Facility).where(Facility.venue_id == venue_id))}
    rows = []
    for key, m in cur.items():
        p = prev.get(key)
        rows.append({"key": key, "name": names.get(key, str(key)), "metrics": _slim(m),
                     "previous": _slim(p) if p and prev_available else None})
    rows.sort(key=lambda r: -(r["metrics"]["net_revenue"] or 0))
    scope_ids = ids if venue_id is None else [venue_id]
    series = analytics.daily_series(db, scope_ids, date_from, date_to)
    total = analytics.finalize(analytics.combine([analytics.compute(db, scope_ids, date_from, date_to)["days"][k]
                                                  for k in analytics.compute(db, scope_ids, date_from, date_to)["days"]]))
    if sport is not None:
        total = analytics.finalize(analytics.combine(
            [m for (fid, _), m in analytics.compute(db, [venue_id], date_from, date_to, with_facilities=True)["facilities"].items()
             if db.get(Facility, fid) and db.get(Facility, fid).sport == sport]))
    return {"level": level, "date_from": date_from, "date_to": date_to, "previous_from": prev_from,
            "previous_to": prev_to, "previous_available": prev_available, "rows": rows, "total": _slim(total),
            "series": [{"date": k, "net_revenue": m["net_revenue"], "occupancy": m["occupancy"],
                        "total_activity": m["total_activity"]} for k, m in series.items()]}


@router.get("/insights")
def insights(date: date | None = None, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    d = day_or_today(date)
    venues = analytics.venues_list(db, None)
    return {"date": d, "insights": anomaly.portfolio(db, venues, d, analytics.now_cutoff(d)),
            "method": anomaly.__doc__}
