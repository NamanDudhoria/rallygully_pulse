"""Performance intelligence, derived entirely from source records (PRD §66).

Nothing here is stored: every metric is recomputed from the operational tables, so
an HQ correction to any record flows through every day, trend and anomaly. Results
are memoized per data version (bumped on every commit that touches the DB).

Definitions (PRD §36-47):
- Operational units: active facilities × 30-min units inside operating hours.
- Sellable = operational − blocked. Occupancy = occupied ÷ sellable.
- Occupied: District/Direct only when attended; Community/Academy/Corporate by allocation.
- Revenue Opportunity: priced value of sellable units with no revenue allocation.
- Net revenue: collected − refunds, attributed to the activity date; Academy is
  accrued at occurrence value until its month is reconciled, then the reconciled
  amount is spread across that month's occurrences.
"""
import statistics
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from ..models import (AcademyOccurrence, AcademyReconciliation, AcademySeries, Allocation, Booking, CommunityGame,
                      CorporateEvent, Facility, OperationalBlock, Participant, Payment, Refund, Venue)
from ..timeutil import at_minute, minute_of, now_local
from .inventory import hours_map
from .pricing import PriceBook

KINDS = ("district", "direct", "community", "academy", "corporate")
METHODS = ("online", "cash", "upi", "card", "other")

# ── memoization keyed on a global data version ───────────────────────────────
_version = 0
_cache: dict = {}
_cache_lock = threading.Lock()


@event.listens_for(Session, "after_flush")
def _bump_version(session, _ctx):
    global _version
    if session.new or session.dirty or session.deleted:
        _version += 1


def _memo(key, fn):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] == _version:
            return hit[1]
    value = fn()
    with _cache_lock:
        if len(_cache) > 256:
            _cache.clear()
        _cache[key] = (_version, value)
    return value


def _blank_day() -> dict:
    return {
        "operational_units": 0, "blocked_units": 0, "sellable_units": 0, "occupied_units": 0,
        "unused_units": 0, "reserved_units": 0, "potential_value": 0.0, "opportunity": 0.0,
        "booking_value": 0.0, "collected": 0.0, "refunds": 0.0, "net_revenue": 0.0, "outstanding": 0.0,
        "academy_accrued": 0.0,
        "activities": {k: 0 for k in (*KINDS, "block")},
        "occupied_by_kind": {k: 0 for k in KINDS},
        "net_by_kind": {k: 0.0 for k in KINDS},
        "collected_by_method": {m: 0.0 for m in METHODS},
        "cancellations": 0, "no_shows": 0, "overdue": 0, "to_collect": 0, "unassigned_district": 0,
        "customers": set(), "returning": set(), "participants": 0,
        # Amount accrued in [h:00, h+1:00) of the activity day; index 0 also holds anything
        # earlier (e.g. prepaid days ahead), 24 anything later. Cumulative sum to H == cutoff H:00.
        "hourly_net": [0.0] * 25, "hourly_occ": [0] * 25,
    }


def first_data_date(db: Session) -> date | None:
    """Pulse starts clean (PRD §48): history begins at the first recorded activity."""
    candidates = [db.scalar(select(func.min(Booking.date))), db.scalar(select(func.min(Allocation.date)))]
    candidates = [c for c in candidates if c]
    return min(candidates) if candidates else None


def compute(db: Session, venue_ids: list[int], start: date, end: date, cutoff_min: int | None = None,
            with_facilities: bool = False) -> dict:
    key = ("compute", tuple(sorted(venue_ids)), start, end, cutoff_min, with_facilities)
    return _memo(key, lambda: _compute(db, venue_ids, start, end, cutoff_min, with_facilities))


def _compute(db: Session, venue_ids: list[int], start: date, end: date, cutoff_min: int | None,
             with_facilities: bool) -> dict:
    """Returns {"days": {(venue_id, date): metrics}, "facilities": {(facility_id, date): metrics}}.

    ``cutoff_min`` limits every day to activity before that minute (intraday comparison).
    """
    out: dict = {}
    fac_out: dict = {}
    if not venue_ids:
        return {"days": out, "facilities": fac_out}
    facilities = list(db.scalars(select(Facility).where(Facility.venue_id.in_(venue_ids), Facility.status == "active")))
    fac_by_id = {f.id: f for f in facilities}
    hours = hours_map(db, venue_ids, start, end)
    book = PriceBook.load(db, venue_ids)
    rule_dates = sorted({r.effective_from for r in book.rules})
    unit_cache: dict = _memo(("unit_cache",), dict)  # shared across computes until data changes

    def unit_value(f: Facility, d: date, slot: int) -> float:
        bucket = sum(1 for rd in rule_dates if rd <= d)
        k = (f.id, d.weekday(), slot, bucket)
        if k not in unit_cache:
            unit_cache[k] = book.unit_value(f, d, slot, d)
        return unit_cache[k]

    def day(v: int, d: date) -> dict:
        k = (v, d)
        if k not in out:
            out[k] = _blank_day()
        return out[k]

    def fday(fid: int, d: date) -> dict:
        k = (fid, d)
        if k not in fac_out:
            fac_out[k] = _blank_day()
        return fac_out[k]

    def cut(d: date, lo: int, hi: int) -> tuple[int, int]:
        """Clip [lo, hi) to the cutoff; for intraday comparisons only."""
        if cutoff_min is None:
            return lo, hi
        return lo, min(hi, cutoff_min)

    def pay_ok(p_at: datetime, d: date) -> bool:
        return cutoff_min is None or p_at <= at_minute(d, cutoff_min)

    # Source records
    bookings = list(db.scalars(select(Booking).where(Booking.venue_id.in_(venue_ids), Booking.date >= start,
                                                     Booking.date <= end)))
    booking_by_id = {b.id: b for b in bookings}
    allocs = list(db.scalars(select(Allocation).where(Allocation.venue_id.in_(venue_ids), Allocation.date >= start,
                                                      Allocation.date <= end, Allocation.active.is_(True))))
    games = {g.id: g for g in db.scalars(select(CommunityGame).where(CommunityGame.venue_id.in_(venue_ids),
                                                                      CommunityGame.date >= start, CommunityGame.date <= end))}
    parts = list(db.scalars(select(Participant).where(Participant.game_id.in_(list(games) or [0]))))
    part_by_id = {p.id: p for p in parts}
    occs = {o.id: o for o in db.scalars(select(AcademyOccurrence).where(AcademyOccurrence.venue_id.in_(venue_ids),
                                                                         AcademyOccurrence.date >= start,
                                                                         AcademyOccurrence.date <= end))}
    events = {e.id: e for e in db.scalars(select(CorporateEvent).where(CorporateEvent.venue_id.in_(venue_ids),
                                                                       CorporateEvent.date >= start, CorporateEvent.date <= end))}
    blocks = list(db.scalars(select(OperationalBlock).where(OperationalBlock.venue_id.in_(venue_ids),
                                                            OperationalBlock.date >= start, OperationalBlock.date <= end)))
    payments = list(db.scalars(select(Payment).where(Payment.venue_id.in_(venue_ids), Payment.activity_date >= start,
                                                     Payment.activity_date <= end, Payment.void.is_(False))))
    refunds = list(db.scalars(select(Refund).where(Refund.booking_id.in_(list(booking_by_id) or [0]),
                                                   Refund.status == "processed")))
    series_ids = {o.series_id for o in occs.values()}
    recs = {(r.series_id, r.month): r for r in db.scalars(select(AcademyReconciliation).where(
        AcademyReconciliation.series_id.in_(list(series_ids) or [0])))}

    # 1. Inventory: operational units and unit values per facility/slot.
    slot_state: dict = {}  # (fid, date, slot) -> "blocked" | "occupied" | "reserved"
    d = start
    while d <= end:
        for f in facilities:
            h = hours.get((f.venue_id, d))
            if not h:
                continue
            lo, hi = cut(d, h[0], h[1])
            n = max(0, (hi - lo) // 30)
            day(f.venue_id, d)["operational_units"] += n
            if with_facilities:
                fday(f.id, d)["operational_units"] += n
        d += timedelta(days=1)

    # 2. Allocations → blocked / occupied / reserved slot state.
    for a in allocs:
        f = fac_by_id.get(a.facility_id)
        h = hours.get((a.venue_id, a.date))
        if not f or not h:
            continue
        if a.kind == "block":
            state = "blocked"
        elif a.kind in ("district", "direct"):
            b = booking_by_id.get(a.ref_id)
            if not b or b.status == "cancelled":
                continue
            state = "occupied" if b.attendance == "attended" else "reserved"
        elif a.kind == "community":
            g = games.get(a.ref_id)
            state = "occupied" if g and g.status != "cancelled" else None
        elif a.kind == "academy":
            o = occs.get(a.ref_id)
            state = "occupied" if o and o.status == "scheduled" else None
        else:
            e = events.get(a.ref_id)
            state = "occupied" if e and e.status != "cancelled" else None
        if not state:
            continue
        lo, hi = cut(a.date, max(a.start_min, h[0]), min(a.end_min, h[1]))
        for slot in range(lo, hi, 30):
            slot_state[(a.facility_id, a.date, slot)] = (state, a.kind)

    d = start
    while d <= end:
        for f in facilities:
            h = hours.get((f.venue_id, d))
            if not h:
                continue
            lo, hi = cut(d, h[0], h[1])
            for slot in range(lo, hi, 30):
                st = slot_state.get((f.id, d, slot))
                targets = [day(f.venue_id, d)] + ([fday(f.id, d)] if with_facilities else [])
                if st and st[0] == "blocked":
                    for t in targets:
                        t["blocked_units"] += 1
                    continue
                val = unit_value(f, d, slot)
                for t in targets:
                    t["potential_value"] += val
                    if st and st[0] == "occupied":
                        t["occupied_units"] += 1
                        t["hourly_occ"][slot // 60] += 1
                        t["occupied_by_kind"][st[1]] += 1
                    elif st and st[0] == "reserved":
                        t["reserved_units"] += 1
                    else:
                        t["unused_units"] += 1
                        t["opportunity"] += val
        d += timedelta(days=1)

    # 3. Revenue & activity
    def started(d: date, start_min: int) -> bool:
        return cutoff_min is None or start_min < cutoff_min

    def split_to_facilities(kind: str, ref_id: int, d: date, amount: float, field: str):
        if not with_facilities:
            return
        fids = [a.facility_id for a in allocs if a.kind == kind and a.ref_id == ref_id]
        for fid in fids:
            fday(fid, d)[field] += amount / len(fids)
            if field == "net_revenue":
                fday(fid, d)["net_by_kind"][kind] += amount / len(fids)

    for b in bookings:
        if not started(b.date, b.start_min):
            continue
        t = day(b.venue_id, b.date)
        if b.status == "cancelled":
            t["cancellations"] += 1
            continue
        t["activities"][b.source] += 1
        t["booking_value"] += b.booking_value
        if b.attendance == "no_show":
            t["no_shows"] += 1
        else:
            if b.payment_status in ("to_collect", "overdue"):
                t["outstanding"] += b.booking_value
            if b.attendance == "attended":
                t["customers"].add(b.customer_id)
        if b.payment_status == "overdue":
            t["overdue"] += 1
        if b.payment_status == "to_collect":
            t["to_collect"] += 1
        if b.source == "district" and b.facility_id is None and b.attendance == "pending":
            t["unassigned_district"] += 1
        if with_facilities and b.facility_id:
            fday(b.facility_id, b.date)["booking_value"] += b.booking_value
            fday(b.facility_id, b.date)["activities"][b.source] += 1

    for g in games.values():
        if g.status == "cancelled":
            if started(g.date, g.start_min):
                day(g.venue_id, g.date)["cancellations"] += 1
            continue
        if started(g.date, g.start_min):
            day(g.venue_id, g.date)["activities"]["community"] += 1
    for p in parts:
        g = games[p.game_id]
        if g.status == "cancelled" or not started(g.date, g.start_min):
            continue
        t = day(g.venue_id, g.date)
        t["participants"] += 1
        if p.attendance != "no_show":
            t["booking_value"] += g.per_person
            split_to_facilities("community", g.id, g.date, g.per_person, "booking_value")
        if p.attendance == "attended":
            t["customers"].add(p.customer_id)
            if p.payment_status != "paid":
                t["outstanding"] += g.per_person
        if p.attendance == "no_show":
            t["no_shows"] += 1

    month_occ_value: dict = defaultdict(float)
    for o in occs.values():
        if o.status == "scheduled":
            month_occ_value[(o.series_id, o.date.strftime("%Y-%m"))] += o.value
    for o in occs.values():
        if not started(o.date, o.start_min):
            continue
        t = day(o.venue_id, o.date)
        if o.status == "cancelled":
            t["cancellations"] += 1
            continue
        if o.status != "scheduled":
            continue
        t["activities"]["academy"] += 1
        t["booking_value"] += o.value
        rec = recs.get((o.series_id, o.date.strftime("%Y-%m")))
        if rec:
            total = month_occ_value[(o.series_id, o.date.strftime("%Y-%m"))] or 1
            share = rec.amount_collected * o.value / total
            t["collected"] += share
            t["collected_by_method"][rec.payment_method] += share
        else:
            share = o.value
            t["academy_accrued"] += share
        t["net_revenue"] += share
        t["hourly_net"][o.start_min // 60] += share
        t["net_by_kind"]["academy"] += share
        split_to_facilities("academy", o.id, o.date, share, "net_revenue")
        split_to_facilities("academy", o.id, o.date, o.value, "booking_value")

    for e in events.values():
        if not started(e.date, e.start_min):
            continue
        t = day(e.venue_id, e.date)
        if e.status == "cancelled":
            t["cancellations"] += 1
            continue
        t["activities"]["corporate"] += 1
        t["booking_value"] += e.amount
        if e.payment_status != "paid":
            t["outstanding"] += e.amount
        split_to_facilities("corporate", e.id, e.date, e.amount, "booking_value")

    for blk in blocks:
        if blk.end_min > blk.start_min and started(blk.date, blk.start_min):
            day(blk.venue_id, blk.date)["activities"]["block"] += 1

    for p in payments:
        if not pay_ok(p.collected_at, p.activity_date):
            continue
        t = day(p.venue_id, p.activity_date)
        if p.source_type == "booking":
            b = booking_by_id.get(p.source_id)
            kind = b.source if b else "direct"
            fid = b.facility_id if b else None
            ref = None
        elif p.source_type == "participant":
            kind, fid = "community", None
            part = part_by_id.get(p.source_id)
            ref = part.game_id if part else None
        else:
            kind, fid, ref = "corporate", None, p.source_id
        t["collected"] += p.amount
        t["net_revenue"] += p.amount
        t["hourly_net"][_hour_bucket(p.collected_at, p.activity_date)] += p.amount
        t["net_by_kind"][kind] += p.amount
        t["collected_by_method"][p.method if p.method in METHODS else "other"] += p.amount
        if with_facilities:
            if fid:
                fday(fid, p.activity_date)["net_revenue"] += p.amount
                fday(fid, p.activity_date)["net_by_kind"][kind] += p.amount
            elif ref:
                split_to_facilities(kind, ref, p.activity_date, p.amount, "net_revenue")

    for r in refunds:
        b = booking_by_id[r.booking_id]
        if cutoff_min is not None and r.processed_at > at_minute(b.date, cutoff_min):
            continue
        t = day(b.venue_id, b.date)  # refunds hit the original activity date (PRD §43)
        t["refunds"] += r.amount
        t["net_revenue"] -= r.amount
        t["hourly_net"][_hour_bucket(r.processed_at, b.date)] -= r.amount
        t["net_by_kind"][b.source] -= r.amount
        if with_facilities and b.facility_id:
            fday(b.facility_id, b.date)["net_revenue"] -= r.amount

    # 4. Repeat customers: lifetime attended consumer interactions up to each day.
    history = _attended_history(db)
    for (v, d), t in out.items():
        t["returning"] = {c for c in t["customers"] if _count_upto(history.get(c, []), d) >= 2}

    for t in list(out.values()) + list(fac_out.values()):
        t["sellable_units"] = t["operational_units"] - t["blocked_units"]
    return {"days": out, "facilities": fac_out}


def _hour_bucket(at: datetime, d: date) -> int:
    m = minute_of(at, d)
    return 0 if m < 0 else 24 if m >= 1440 else m // 60


def _attended_history(db: Session) -> dict[int, list[date]]:
    def build():
        h: dict[int, list[date]] = defaultdict(list)
        for cid, d in db.execute(select(Booking.customer_id, Booking.date).where(
                Booking.status == "confirmed", Booking.attendance == "attended")):
            h[cid].append(d)
        for cid, d in db.execute(select(Participant.customer_id, CommunityGame.date).join(
                CommunityGame, CommunityGame.id == Participant.game_id).where(
                Participant.attendance == "attended", CommunityGame.status != "cancelled")):
            h[cid].append(d)
        for v in h.values():
            v.sort()
        return dict(h)
    return _memo(("history",), build)


def _count_upto(dates: list[date], d: date) -> int:
    import bisect
    return bisect.bisect_right(dates, d)


def repeat_rate(db: Session, upto: date, since: date | None = None, venue_ids: list[int] | None = None) -> dict:
    """PRD §47: customers with ≥2 attended consumer interactions ÷ customers with ≥1.

    Scoped to customers active (attended) in [since, upto] when ``since`` is given;
    lifetime counts are used to decide whether each is a returning customer.
    """
    def build():
        hist = _attended_history(db)
        if venue_ids is not None or since is not None:
            active = _active_customers(db, since or date.min, upto, venue_ids)
        else:
            active = {c for c, ds in hist.items() if ds and ds[0] <= upto}
        total = len(active)
        returning = sum(1 for c in active if _count_upto(hist.get(c, []), upto) >= 2)
        return {"customers": total, "returning": returning, "rate": returning / total if total else None}
    return _memo(("repeat", upto, since, tuple(venue_ids) if venue_ids else None), build)


def _active_customers(db: Session, since: date, upto: date, venue_ids: list[int] | None) -> set[int]:
    q1 = select(Booking.customer_id).where(Booking.status == "confirmed", Booking.attendance == "attended",
                                           Booking.date >= since, Booking.date <= upto)
    q2 = select(Participant.customer_id).join(CommunityGame, CommunityGame.id == Participant.game_id).where(
        Participant.attendance == "attended", CommunityGame.status != "cancelled",
        CommunityGame.date >= since, CommunityGame.date <= upto)
    if venue_ids is not None:
        q1 = q1.where(Booking.venue_id.in_(venue_ids))
        q2 = q2.where(CommunityGame.venue_id.in_(venue_ids))
    return set(db.scalars(q1)) | set(db.scalars(q2))


# ── aggregation helpers ──────────────────────────────────────────────────────
ADDITIVE = ("operational_units", "blocked_units", "sellable_units", "occupied_units", "unused_units",
            "reserved_units", "potential_value", "opportunity", "booking_value", "collected", "refunds",
            "net_revenue", "outstanding", "academy_accrued", "cancellations", "no_shows", "overdue",
            "to_collect", "unassigned_district", "participants")


def combine(days: list[dict]) -> dict:
    """Sum day metrics. Ratios are computed from totals, never averaged (PRD §38)."""
    t = _blank_day()
    for d in days:
        for k in ADDITIVE:
            t[k] += d[k]
        for group in ("activities", "occupied_by_kind", "net_by_kind", "collected_by_method"):
            for k, v in d[group].items():
                t[group][k] += v
        t["customers"] |= d["customers"]
        t["returning"] |= d["returning"]
        for i in range(25):
            t["hourly_net"][i] += d["hourly_net"][i]
            t["hourly_occ"][i] += d["hourly_occ"][i]
    return t


def finalize(t: dict) -> dict:
    """JSON-ready metrics with derived ratios."""
    out = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in t.items()
           if k not in ("customers", "returning")}
    out["occupancy"] = t["occupied_units"] / t["sellable_units"] if t["sellable_units"] else None
    # Utilization: share of physical capacity consumed for any reason (activity or block).
    out["utilization"] = ((t["occupied_units"] + t["blocked_units"]) / t["operational_units"]
                          if t["operational_units"] else None)
    out["unique_customers"] = len(t["customers"])
    out["returning_customers"] = len(t["returning"])
    out["day_repeat_rate"] = len(t["returning"]) / len(t["customers"]) if t["customers"] else None
    out["total_activity"] = sum(v for k, v in t["activities"].items() if k != "block")
    offline = t["collected_by_method"]["cash"]
    out["cash_share"] = offline / t["collected"] if t["collected"] else None
    out["hours_operational"] = t["operational_units"] / 2
    return out


# Metric extractors used by comparisons & anomaly detection.
METRICS = {
    "net_revenue": lambda m: m["net_revenue"],
    "collected": lambda m: m["collected"],
    "occupancy": lambda m: m["occupancy"],
    "occupied_units": lambda m: m["occupied_units"],
    "utilization": lambda m: m["utilization"],
    "total_activity": lambda m: m["total_activity"],
    "opportunity": lambda m: m["opportunity"],
    "unique_customers": lambda m: m["unique_customers"],
    "cash_share": lambda m: m["cash_share"],
    "no_shows": lambda m: m["no_shows"],
    "cancellations": lambda m: m["cancellations"],
    "day_repeat_rate": lambda m: m["day_repeat_rate"],
}


SHARED_WINDOW = 120


def _all_venue_ids(db: Session) -> list[int]:
    return _memo(("venue_ids",), lambda: sorted(db.scalars(select(Venue.id))))


def raw_days(db: Session, venue_ids: list[int], start: date, end: date, cutoff_min: int | None = None) -> dict:
    """Unfinalized {(venue_id, date): metrics}. Requests ending near today share one all-venue
    pass anchored on today (memoized), so dashboards don't recompute per venue / per window."""
    anchor = max(end, now_local().date())
    if (anchor - start).days < SHARED_WINDOW:
        return compute(db, _all_venue_ids(db), anchor - timedelta(days=SHARED_WINDOW - 1), anchor, cutoff_min)["days"]
    return compute(db, venue_ids, start, end, cutoff_min)["days"]


def daily_series(db: Session, venue_ids: list[int], start: date, end: date, cutoff_min: int | None = None) -> dict[date, dict]:
    """{date: finalized metrics} combined across ``venue_ids``; only days since Pulse began."""
    first = first_data_date(db)
    if first is None:
        return {}
    start = max(start, first)
    if start > end:
        return {}
    raw = raw_days(db, venue_ids, start, end, cutoff_min)
    wanted = set(venue_ids)
    by_day: dict = defaultdict(list)
    for (v, d), m in raw.items():
        if v in wanted and start <= d <= end:
            by_day[d].append(m)
    out = {}
    d = start
    while d <= end:
        out[d] = finalize(combine(by_day.get(d, [])))
        d += timedelta(days=1)
    return out


WINDOWS = {"prev_day": 1, "same_weekday": 4, "d7": 7, "d30": 30, "d90": 90}
MIN_SAMPLES = {"prev_day": 1, "same_weekday": 1, "d7": 3, "d30": 7, "d90": 30}


def comparisons(db: Session, venue_ids: list[int], d: date, metrics: list[str], cutoff_min: int | None = None) -> dict:
    """Current value vs each historical baseline: absolute & % change, with sample size.

    With ``cutoff_min`` both current and history are cut at the same time of day (PRD §51).
    Windows without enough history return ``available: false`` instead of a misleading number.
    """
    series = daily_series(db, venue_ids, d - timedelta(days=90), d, cutoff_min)
    cur = series.get(d)
    result = {}
    for name in metrics:
        f = METRICS[name]
        current = f(cur) if cur else None
        windows = {}
        for w, span in WINDOWS.items():
            if w == "same_weekday":
                days = [d - timedelta(days=7 * i) for i in range(1, 5)]
            else:
                days = [d - timedelta(days=i) for i in range(1, span + 1)]
            vals = [f(series[x]) for x in days if x in series and series[x]["operational_units"] > 0
                    and f(series[x]) is not None]
            n = len(vals)
            if n < MIN_SAMPLES[w] or current is None:
                windows[w] = {"available": False, "samples": n, "required": MIN_SAMPLES[w]}
                continue
            base = sum(vals) / n
            windows[w] = {"available": True, "samples": n, "full": n >= min(span, 4 if w == "same_weekday" else span),
                          "baseline": base, "abs_change": current - base,
                          "pct_change": (current - base) / base if base else None}
        result[name] = {"current": current, "windows": windows}
    return result


def history_days(db: Session) -> int:
    first = first_data_date(db)
    return (now_local().date() - first).days if first else 0


def venues_list(db: Session, venue_ids: list[int] | None) -> list[Venue]:
    q = select(Venue).order_by(Venue.id)
    if venue_ids is not None:
        q = q.where(Venue.id.in_(venue_ids))
    return list(db.scalars(q))


def now_cutoff(d: date) -> int | None:
    """Minute-of-day cutoff when ``d`` is today (incomplete day), else None."""
    now = now_local()
    return minute_of(now, d) if d == now.date() else None


def series_ids_for_academy(db: Session) -> list[AcademySeries]:
    return list(db.scalars(select(AcademySeries)))


def median(vals):
    return statistics.median(vals) if vals else None
