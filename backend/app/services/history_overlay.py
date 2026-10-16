"""Imported sales history as daily revenue, so dashboards can cover dates before Pulse went live.

Day-dated lines land on their day. Weekly and monthly lines are spread evenly over the days they cover,
so whole months are exact and part-month windows are estimates. A venue-month that has live Pulse revenue
is never taken from history, so nothing is counted twice.
"""
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import HistBooking, HistRevenue, HistSlot
from ..timeutil import today
from . import analytics

# History channel → the dashboard's channel kinds (the first five exist live; the last two are history-only).
KIND_OF = {
    "district": "district",
    "offline_bookings": "direct",
    "pickup_games": "community", "social_events": "community", "community_partnerships": "community",
    "academy": "academy", "coaching": "academy",
    "corporate_events": "corporate", "b2b_partnerships": "corporate", "brand_activation": "corporate",
    "hudle": "platforms", "playo": "platforms", "khelomore": "platforms", "zplys": "platforms",
    "courtly": "platforms", "sportomic": "platforms", "misfits": "platforms",
}
HISTORY_KINDS = ("platforms", "other")
# Imported sport labels → the facility sports used by live data (box cricket is played on the turf).
SPORT_OF = {"Turf": "Football", "Cricket": "Football"}


def kind_of(channel: str) -> str:
    return KIND_OF.get(channel, "other")


def live_venue_months(db: Session, venue_ids: list[int]) -> set:
    """{(venue_id, 'YYYY-MM')} that already have live Pulse revenue."""
    first = analytics.first_data_date(db)
    if not first:
        return set()
    return {(v, d.strftime("%Y-%m")) for (v, d), m in analytics.raw_days(db, venue_ids, first, today()).items()
            if m["net_revenue"]}


def lines(db: Session, venue_ids: list[int], start: date, end: date) -> list:
    """History lines overlapping [start, end] for venue-months with no live revenue."""
    live = live_venue_months(db, venue_ids)
    q = select(HistRevenue).where(HistRevenue.venue_id.in_(venue_ids), HistRevenue.date_from <= end,
                                  HistRevenue.date_to >= start)
    return [l for l in db.scalars(q) if (l.venue_id, f"{l.year:04d}-{l.month:02d}") not in live]


def spread(l, start: date, end: date):
    """(date, amount) for the part of a line inside [start, end], spread evenly over its days."""
    span = (l.date_to - l.date_from).days + 1
    per = l.gross / span
    lo, hi = max(l.date_from, start), min(l.date_to, end)
    for i in range((hi - lo).days + 1):
        yield lo + timedelta(days=i), per


def totals(db: Session, venue_ids: list[int], start: date, end: date) -> dict:
    """{'by_venue': {vid: x}, 'by_sport': {sport: x}, 'by_day': {date: x}, 'by_kind': {kind: x}, 'total': x}."""
    out = {"by_venue": defaultdict(float), "by_sport": defaultdict(float), "by_day": defaultdict(float),
           "by_kind": defaultdict(float), "by_venue_kind": defaultdict(lambda: defaultdict(float)), "total": 0.0}
    for l in lines(db, venue_ids, start, end):
        for d, x in spread(l, start, end):
            out["by_venue"][l.venue_id] += x
            out["by_sport"][SPORT_OF.get(l.sport, l.sport) or "Unspecified"] += x
            out["by_day"][d] += x
            out["by_kind"][kind_of(l.channel)] += x
            out["by_venue_kind"][l.venue_id][kind_of(l.channel)] += x
            out["total"] += x
    out["occ"] = occupancy(db, venue_ids, start, end)
    return out


def _blank_occ():
    return {"occupied_units": 0.0, "sellable_units": 0.0, "bookings": 0, "customers": set()}


def occupancy(db: Session, venue_ids: list[int], start: date, end: date) -> dict:
    """Court time from the imported platform bookings (recorded + imputed offline play), in 30-minute units,
    with booking counts and approximate customers — by venue, sport, day and overall."""
    live = live_venue_months(db, venue_ids)
    keep = lambda vid, d: (vid, d.strftime("%Y-%m")) not in live  # noqa: E731
    out = {k: defaultdict(_blank_occ) for k in ("by_venue", "by_sport", "by_day")}
    out["total"] = _blank_occ()
    out["by_venue_day"] = defaultdict(_blank_occ)
    q = select(HistSlot.venue_id, HistSlot.day, HistSlot.sport, HistSlot.capacity_min, HistSlot.recorded_min,
               HistSlot.imputed_min).where(HistSlot.venue_id.in_(venue_ids), HistSlot.day >= start, HistSlot.day <= end)
    for vid, d, sp, cap, rec, imp in db.execute(q):
        if not keep(vid, d):
            continue
        occ = min(cap, rec + imp) / 30 if cap else 0.0
        for o in (out["by_venue"][vid], out["by_sport"][sp], out["by_day"][d], out["total"], out["by_venue_day"][(vid, d)]):
            o["occupied_units"] += occ
            o["sellable_units"] += cap / 30
    q = select(HistBooking.venue_id, HistBooking.day, HistBooking.sport, HistBooking.customer_key).where(
        HistBooking.venue_id.in_(venue_ids), HistBooking.day >= start, HistBooking.day <= end)
    for vid, d, sp, ck in db.execute(q):
        if not keep(vid, d):
            continue
        for o in (out["by_venue"][vid], out["by_sport"][sp], out["by_day"][d], out["total"], out["by_venue_day"][(vid, d)]):
            o["bookings"] += 1
            if ck:
                o["customers"].add(ck)
    return out


def has_any(db: Session, venue_ids: list[int], start: date, end: date) -> bool:
    return bool(lines(db, venue_ids, start, end))


def with_revenue(m: dict | None, total: float, by_kind: dict, occ: dict | None = None) -> dict:
    """Finalized day metrics (or an empty day) with imported revenue — and, when the platform bookings cover
    it, court time, bookings and customers — added. Payments and collections stay unknown for these days."""
    live = bool(m and (m.get("net_revenue") or m.get("booking_value")))
    m = m if m else analytics.finalize(analytics.combine([]))
    m["from_history"] = not live
    m["net_revenue"] = round((m.get("net_revenue") or 0) + total, 2)
    for k, v in by_kind.items():
        m["net_by_kind"][k] = round(m["net_by_kind"].get(k, 0) + v, 2)
    if occ and occ["sellable_units"]:
        m["occupied_units"] = round((m.get("occupied_units") or 0) + occ["occupied_units"], 2)
        m["sellable_units"] = round((m.get("sellable_units") or 0) + occ["sellable_units"], 2)
        m["operational_units"] = round((m.get("operational_units") or 0) + occ["sellable_units"], 2)
        m["occupancy"] = m["occupied_units"] / m["sellable_units"] if m["sellable_units"] else None
        m["utilization"] = m["occupancy"]
        m["total_activity"] = (m.get("total_activity") or 0) + occ["bookings"]
        m["unique_customers"] = (m.get("unique_customers") or 0) + len(occ["customers"])
        m["history_occupancy"] = True
    return m


def trend_rows(series: dict, by_day: dict, keys: tuple, occ_by_day: dict | None = None) -> list[dict]:
    """Daily trend rows from live metrics plus imported revenue (and court time) for days Pulse didn't record."""
    rows = []
    occ_by_day = occ_by_day or {}
    for d in sorted(set(series) | set(by_day)):
        m = series.get(d)
        row = {"date": d, **{k: (m[k] if m else None) for k in keys}}
        row["net_revenue"] = round(((m["net_revenue"] if m else 0) or 0) + by_day.get(d, 0), 2)
        o = occ_by_day.get(d)
        if not m and o and o["sellable_units"]:
            if "occupancy" in keys:
                row["occupancy"] = o["occupied_units"] / o["sellable_units"]
            if "total_activity" in keys:
                row["total_activity"] = o["bookings"]
        rows.append(row)
    return rows
