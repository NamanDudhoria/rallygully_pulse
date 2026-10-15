"""Imported sales history as daily revenue, so dashboards can cover dates before Pulse went live.

Day-dated lines land on their day. Weekly and monthly lines are spread evenly over the days they cover,
so whole months are exact and part-month windows are estimates. A venue-month that has live Pulse revenue
is never taken from history, so nothing is counted twice.
"""
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import HistRevenue
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
            out["by_sport"][l.sport or "Unspecified"] += x
            out["by_day"][d] += x
            out["by_kind"][kind_of(l.channel)] += x
            out["by_venue_kind"][l.venue_id][kind_of(l.channel)] += x
            out["total"] += x
    return out


def has_any(db: Session, venue_ids: list[int], start: date, end: date) -> bool:
    return bool(lines(db, venue_ids, start, end))


def with_revenue(m: dict | None, total: float, by_kind: dict) -> dict:
    """Finalized day metrics (or an empty day) with imported revenue added."""
    live = bool(m and (m.get("net_revenue") or m.get("booking_value")))
    m = m if m else analytics.finalize(analytics.combine([]))
    m["from_history"] = not live  # only revenue is known; bookings, payments and occupancy are not
    m["net_revenue"] = round((m.get("net_revenue") or 0) + total, 2)
    for k, v in by_kind.items():
        m["net_by_kind"][k] = round(m["net_by_kind"].get(k, 0) + v, 2)
    return m


def trend_rows(series: dict, by_day: dict, keys: tuple) -> list[dict]:
    """Daily trend rows from live metrics plus imported revenue for days Pulse didn't record."""
    rows = []
    for d in sorted(set(series) | set(by_day)):
        m = series.get(d)
        row = {"date": d, **{k: (m[k] if m else None) for k in keys}}
        row["net_revenue"] = round(((m["net_revenue"] if m else 0) or 0) + by_day.get(d, 0), 2)
        rows.append(row)
    return rows
