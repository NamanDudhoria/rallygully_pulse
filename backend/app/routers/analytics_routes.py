"""Portfolio dashboard, drill-down (portfolio → venue → sport → facility), trends and insights."""
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..permissions import require
from ..db import get_db
from ..models import Booking, DayClose, Facility, HistRevenue, User
from ..services import analytics, anomaly, bookings, dayclose, history_overlay, inventory
from ..timeutil import hhmm, now_local, today
from .common import custom_window, day_or_today
from .venues import DAY_METRICS, intraday_curve

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _slim(m: dict) -> dict:
    keys = ("net_revenue", "collected", "booking_value", "refunds", "outstanding", "opportunity", "potential_value",
            "occupancy", "utilization", "occupied_units", "sellable_units", "operational_units", "blocked_units",
            "unused_units", "reserved_units", "total_activity", "unique_customers", "returning_customers",
            "no_shows", "cancellations", "overdue", "unassigned_district", "cash_share", "activities",
            "net_by_kind", "occupied_by_kind", "collected_by_method", "academy_accrued", "from_history")
    return {k: m.get(k) for k in keys}


@router.get("/portfolio")
def portfolio(date: date | None = None, cmp_from: date | None = None, cmp_to: date | None = None,
              user: User = Depends(require("portfolio.view")), db: Session = Depends(get_db)):
    d = day_or_today(date)
    custom = custom_window(cmp_from, cmp_to)
    bookings.refresh_overdue(db)
    db.commit()
    venues = analytics.venues_list(db, None)
    ids = [v.id for v in venues]
    is_today = d == today()
    cutoff = analytics.now_cutoff(d)
    total = analytics.daily_series(db, ids, d, d).get(d)
    now_total = analytics.daily_series(db, ids, d, d, cutoff).get(d) if is_today else None
    per_venue = analytics.raw_days(db, ids, d, d)
    per_venue_now = analytics.raw_days(db, ids, d, d, cutoff) if is_today else per_venue
    hist = history_overlay.totals(db, ids, d, d)  # imported revenue for a past date Pulse didn't record
    if hist["total"]:
        total = history_overlay.with_revenue(total, hist["total"], hist["by_kind"])
    rows = []
    for v in venues:
        src = per_venue_now if is_today else per_venue
        m = analytics.finalize(analytics.combine([src[(v.id, d)]] if (v.id, d) in src else []))
        if hist["by_venue"].get(v.id):
            m = history_overlay.with_revenue(m, hist["by_venue"][v.id], hist["by_venue_kind"][v.id])
        cmp_ = analytics.comparisons(db, [v.id], d, ["net_revenue", "occupancy", "total_activity"],
                                     cutoff if is_today else None, custom)
        rows.append({"venue": {"id": v.id, "name": v.name, "code": v.code, "location": v.location},
                     "hours": _hours(db, v.id, d), "metrics": _slim(m), "comparisons": cmp_,
                     "day_close": dayclose.status(db, v.id, d)["status"]})
    trend = analytics.daily_series(db, ids, d - timedelta(days=89), d)
    trend_hist = history_overlay.totals(db, ids, d - timedelta(days=89), d)["by_day"]
    insights = anomaly.portfolio(db, venues, d, cutoff) if total and not hist["total"] else []
    if is_today:
        insights += anomaly.portfolio(db, venues, d - timedelta(days=1))
    hours = [h for h in (inventory.hours_for(db, v.id, d) for v in venues) if h]
    span = (min(h[0] for h in hours), max(h[1] for h in hours)) if hours else None
    return {
        "date": d, "is_today": is_today, "comparison_mode": "intraday" if is_today else "full_day",
        "cutoff_label": hhmm(cutoff) if cutoff is not None else None,
        "metrics": _slim(total) if total else None,
        "metrics_now": _slim(now_total) if is_today and now_total else None,
        "comparisons": analytics.comparisons(db, ids, d, DAY_METRICS, cutoff if is_today else None, custom),
        "repeat": analytics.repeat_rate(db, d, d - timedelta(days=89)),
        "repeat_lifetime": analytics.repeat_rate(db, d),
        "venues": rows,
        "trend": history_overlay.trend_rows(trend, trend_hist, ("occupancy", "opportunity", "total_activity")),
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
          compare_from: date | None = None, compare_to: date | None = None,
          user: User = Depends(require("portfolio.view")), db: Session = Depends(get_db)):
    """One level of the drill-down against a comparison period.

    The comparison defaults to the equal-length period just before; pass compare_from/compare_to
    for another (e.g. month-to-date vs the same days of last month).
    """
    allowed = None
    if date_to < date_from or (date_to - date_from).days > 366:
        raise HTTPException(422, "Choose a range of up to one year.")
    if (compare_from is None) != (compare_to is None):
        raise HTTPException(422, "Give both compare_from and compare_to.")
    if compare_from is not None:
        if compare_to < compare_from or (compare_to - compare_from).days > 366:
            raise HTTPException(422, "Choose a comparison range of up to one year.")
        prev_from, prev_to = compare_from, compare_to
    else:
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
    scope = ids if venue_id is None else [venue_id]
    hist_cur = history_overlay.totals(db, scope, date_from, date_to)
    hist_prev = history_overlay.totals(db, scope, prev_from, prev_to)
    prev_available = bool(first and first <= prev_from) or bool(hist_prev["total"])
    if venue_id is None:
        hkey = lambda src: src["by_venue"]  # noqa: E731
    elif sport is None:
        hkey = lambda src: src["by_sport"]  # noqa: E731
    else:
        hkey = lambda src: {}  # noqa: E731 — history has no facility detail
    for rows_, hist in ((cur, hist_cur), (prev, hist_prev)):
        for k, x in hkey(hist).items():
            if k not in rows_ and venue_id is None and k not in {v.id for v in venues}:
                continue
            m = rows_.setdefault(k, analytics.finalize(analytics.combine([])))
            m["net_revenue"] = round((m.get("net_revenue") or 0) + x, 2)
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

    def total_for(start, end):
        if sport is not None:
            sport_fids = {f.id for f in db.scalars(select(Facility).where(Facility.venue_id == venue_id,
                                                                         Facility.sport == sport))}
            return analytics.finalize(analytics.combine(
                [m for (fid, _), m in analytics.compute(db, [venue_id], start, end, with_facilities=True)["facilities"].items()
                 if fid in sport_fids]))
        return analytics.finalize(analytics.combine(list(analytics.compute(db, scope_ids, start, end)["days"].values())))

    def with_history(m, hist):
        x = hist["total"] if sport is None else hist["by_sport"].get(sport, 0.0)
        m["net_revenue"] = round((m.get("net_revenue") or 0) + x, 2)
        if sport is None:
            for k, v in hist["by_kind"].items():
                m["net_by_kind"][k] = round(m["net_by_kind"].get(k, 0) + v, 2)
        return m

    total = with_history(total_for(date_from, date_to), hist_cur)
    prev_total = with_history(total_for(prev_from, prev_to), hist_prev) if prev_available else None
    live_days = {k: m["net_revenue"] for k, m in series.items()}
    by_day = {d: (live_days.get(d, 0) + (hist_cur["by_day"].get(d, 0) if sport is None else 0))
              for d in set(live_days) | (set(hist_cur["by_day"]) if sport is None else set())}
    return {"level": level, "date_from": date_from, "date_to": date_to, "previous_from": prev_from,
            "previous_to": prev_to, "previous_available": prev_available, "rows": rows, "total": _slim(total),
            "previous_total": _slim(prev_total) if prev_total else None,
            "series": [{"date": d, "net_revenue": round(by_day[d], 2),
                        "occupancy": series[d]["occupancy"] if d in series else None,
                        "total_activity": series[d]["total_activity"] if d in series else None}
                       for d in sorted(by_day)]}


@router.get("/monthly")
def monthly(venue_id: int | None = None, user: User = Depends(require("portfolio.view")), db: Session = Depends(get_db)):
    """Revenue by month: imported history where it exists, live Pulse for later months.

    A venue-month takes one source only — history if it has any, otherwise live — so nothing is counted twice.
    """
    venues = [v for v in analytics.venues_list(db, None) if venue_id is None or v.id == venue_id]
    if venue_id is not None and not venues:
        raise HTTPException(404, "Venue not found")
    ids = [v.id for v in venues]
    cells: dict = defaultdict(float)
    source: dict = {}
    for vid, y, m, g in db.execute(select(HistRevenue.venue_id, HistRevenue.year, HistRevenue.month, HistRevenue.gross)
                                   .where(HistRevenue.venue_id.in_(ids))):
        k = (vid, f"{y:04d}-{m:02d}")
        cells[k] += g
        source[k] = "history"
    first = analytics.first_data_date(db)
    if first:
        for (vid, d), m in analytics.raw_days(db, ids, first, today()).items():
            k = (vid, d.strftime("%Y-%m"))
            if source.get(k) == "history":
                continue
            cells[k] += m["net_revenue"]
            source[k] = "live"
    months = sorted({k[1] for k in cells})
    current = today().strftime("%Y-%m")
    return {
        "months": [{"month": mk, "revenue": round(sum(cells.get((v.id, mk), 0) for v in venues), 2),
                    "partial": mk == current, "live": any(source.get((v.id, mk)) == "live" for v in venues)}
                   for mk in months],
        "venues": [{"id": v.id, "name": v.name, "code": v.code,
                    "months": {mk: round(cells[(v.id, mk)], 2) for mk in months if (v.id, mk) in cells}} for v in venues
                   if any((v.id, mk) in cells for mk in months)],
    }


@router.get("/insights")
def insights(date: date | None = None, user: User = Depends(require("portfolio.view")), db: Session = Depends(get_db)):
    d = day_or_today(date)
    venues = analytics.venues_list(db, None)
    return {"date": d, "insights": anomaly.portfolio(db, venues, d, analytics.now_cutoff(d)),
            "method": anomaly.__doc__}
