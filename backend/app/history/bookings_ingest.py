"""Import the platform booking exports and build historical occupancy.

    python -m app.history.bookings_ingest ../online_bookings [--report ONLINE_BOOKINGS_REPORT.md]

Rules (agreed with HQ, 16 Oct 2026):
- Revenue is never changed. The sales files / trackers stay the only source of revenue; bookings add when,
  where (court, sport) and who. Where a platform's export disagrees with the sales file, the gap is reported.
- Recorded bookings fill court time as they are. Offline pay & play money that no recorded offline booking
  explains is turned into hours at that venue-month's average price per hour for each sport (from its online
  bookings), split by sport in proportion to online revenue, and spread over weekday x hour in the venue's own
  booking pattern. No court-hour goes past 100%; whatever cannot be placed is reported.
- Capacity is today's courts per sport x the hours the venue actually ran that month (earliest booking start to
  latest end). Unique customers are matched by normalised name within a venue (approximate).
"""
import argparse
import calendar
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import delete, select

from ..db import SessionLocal, ensure_schema
from ..models import Facility, HistBooking, HistBookingMonth, HistRevenue, HistSlot, Venue
from .bookings_parse import name_key, parse_folder

# Booking sport → the facility sport whose courts it uses (box cricket is played on the turf).
FACILITY_SPORT = {"Pickleball": "Pickleball", "Padel": "Padel", "Turf": "Football", "Cricket": "Football",
                  "Football": "Football"}
HUDLE_REVENUE = ("online paid amount", "hudle wallet", "hudle passes", "qr payment")  # money Hudle collected
MIN_PRICE_SAMPLE = 5
# Court-booking channels (pay & play). Their money not explained by recorded bookings is turned into hours.
PAY_AND_PLAY = ("offline_bookings", "district", "hudle", "playo", "khelomore", "zplys", "sportomic", "courtly")  # bookings needed before a venue-month's own price per hour is trusted


def _revenue(b) -> float:
    """The part of a booking's money that is revenue of its channel (no membership wallet credit)."""
    if b.platform == "hudle":
        if b.medium == "offline":
            return b.parts.get("pay at venue", 0)
        return sum(b.parts.get(k, 0) for k in HUDLE_REVENUE)
    return b.amount


def _hour_split(start: int, end: int):
    """(hour, minutes) pieces of a time range; minutes past midnight stay on the same day's last hour."""
    t = start
    while t < end:
        h = min(t // 60, 23)
        nxt = min(end, (t // 60 + 1) * 60) if t // 60 < 23 else end
        yield h, nxt - t
        t = nxt


def run(folder: str, report_path: str | None = None) -> dict:
    ensure_schema()
    db = SessionLocal()
    try:
        for model in (HistSlot, HistBooking, HistBookingMonth):
            db.execute(delete(model))
        res, files = parse_folder(folder)
        venues = {v.code: v for v in db.scalars(select(Venue))}
        courts = defaultdict(dict)  # venue_id → {facility sport: courts}
        for f in db.scalars(select(Facility).where(Facility.status == "active")):
            courts[f.venue_id][f.sport] = courts[f.venue_id].get(f.sport, 0) + 1

        # 1. Bookings, with sport resolved to a facility sport the venue has.
        rows, no_venue, sport_guess = [], defaultdict(int), defaultdict(int)
        for b in res.bookings:
            v = venues.get(b.venue)
            if v is None:
                no_venue[b.venue] += 1
                continue
            fs = FACILITY_SPORT.get(b.sport) if b.sport else None
            have = courts.get(v.id, {})
            if fs not in have:
                guess = FACILITY_SPORT.get(_sport_from_court(b.court)) if b.court else None
                fs = guess if guess in have else (max(have, key=have.get) if have else (fs or "Pickleball"))
                sport_guess[v.code] += 1
            rows.append((v, fs, b))
            db.add(HistBooking(venue_id=v.id, day=b.day, start_min=b.start_min, end_min=b.end_min, sport=fs,
                               court=b.court[:80], platform=b.platform, medium=b.medium, amount=round(b.amount, 2),
                               revenue=round(_revenue(b), 2), customer=b.customer[:120],
                               customer_key=f"{v.id}:{name_key(b.customer)}" if name_key(b.customer) else "",
                               ref=str(b.ref)[:60], file=b.file[:200], sheet=str(b.sheet)[:80], row=b.row))
        db.flush()

        # 2. Ledger figures for the comparison and the offline money to place.
        ledger = defaultdict(float)
        for vid, y, m, ch, g in db.execute(select(HistRevenue.venue_id, HistRevenue.year, HistRevenue.month,
                                                  HistRevenue.channel, HistRevenue.gross)):
            ledger[(vid, y, m, ch)] += g

        by_vm = defaultdict(list)
        for v, fs, b in rows:
            by_vm[(v.id, b.day.year, b.day.month)].append((fs, b))
        by_venue = defaultdict(list)
        for v, fs, b in rows:
            by_venue[v.id].append((fs, b))

        # Venue-level booking pattern per sport: weight of each (weekday, hour) — the stratified average.
        pattern = {}
        for vid, items in by_venue.items():
            w = defaultdict(lambda: defaultdict(float))
            for fs, b in items:
                for h, mins in _hour_split(b.start_min, b.end_min):
                    w[fs][(b.day.weekday(), h)] += mins
                    w["*"][(b.day.weekday(), h)] += mins
            pattern[vid] = w

        # Portfolio fallback price per hour by sport.
        port_price = _price(rows_all=[(fs, b) for _, fs, b in rows])
        out = {"bookings": len(rows), "venue_months": 0, "imputed_hours": 0.0, "unplaced_hours": 0.0}
        months = set(by_vm) | {(vid, y, m) for (vid, y, m, ch) in ledger if ch in PAY_AND_PLAY and vid in by_venue}
        for (vid, y, m) in sorted(months):
            items = by_vm.get((vid, y, m), [])
            audit = _venue_month(db, vid, y, m, items, by_venue[vid], pattern[vid], courts.get(vid, {}), ledger,
                                 port_price)
            db.add(HistBookingMonth(venue_id=vid, year=y, month=m, data=audit))
            out["venue_months"] += 1
            out["imputed_hours"] += audit["imputed_hours"]
            out["unplaced_hours"] += audit["unplaced_hours"]
        db.commit()
        out["imputed_hours"] = round(out["imputed_hours"], 1)
        out["unplaced_hours"] = round(out["unplaced_hours"], 1)
        out["skipped"] = res.skipped
        out["sport_guessed"] = dict(sport_guess)
        out["no_venue"] = dict(no_venue)
        if report_path:
            write_report(db, report_path, out, files)
        return out
    finally:
        db.close()


def _sport_from_court(court: str) -> str | None:
    from .bookings_parse import sport_of
    return sport_of(court)


def _price(rows_all) -> dict:
    """Average online price per hour by sport over the given bookings."""
    money, mins = defaultdict(float), defaultdict(float)
    for fs, b in rows_all:
        if b.medium == "online" and _revenue(b) > 0:
            money[fs] += _revenue(b)
            mins[fs] += b.end_min - b.start_min
    return {s: money[s] / (mins[s] / 60) for s in money if mins[s]}


def _venue_month(db, vid, y, m, items, venue_items, pattern, courts, ledger, port_price) -> dict:
    ndays = calendar.monthrange(y, m)[1]
    days = [date(y, m, d) for d in range(1, ndays + 1)]

    # Opening hours this month: earliest start to latest end of any recorded booking (venue-wide fallback).
    open_h, close_h = _open_hours(items if len(items) >= 20 else venue_items)
    hours = range(open_h, close_h)

    cap = {(d, h, s): n * 60.0 for d in days for h in hours for s, n in courts.items()}
    rec = defaultdict(float)
    for fs, b in items:
        for h, mins in _hour_split(b.start_min, b.end_min):
            rec[(b.day, h, fs)] += mins
    for d, h, _ in list(rec):  # a booked hour outside the usual window was open that day
        for s_, n in courts.items():
            cap.setdefault((d, h, s_), n * 60.0)

    # Platform totals vs the sales file (reported only).
    plat = defaultdict(float)
    for fs, b in items:
        plat[("offline_bookings" if b.medium == "offline" else b.platform)] += _revenue(b)
    compare = {ch: {"export": round(x, 2), "sales_file": round(ledger.get((vid, y, m, ch), 0), 2),
                    "gap": round(x - ledger.get((vid, y, m, ch), 0), 2)} for ch, x in plat.items()}

    # Pay & play money not explained by recorded bookings (offline counter sales, months with no export)
    # → hours to impute. A channel whose export exceeds the sales file explains nothing extra.
    unexplained = {ch: max(0.0, ledger.get((vid, y, m, ch), 0.0) - plat.get(ch, 0.0)) for ch in PAY_AND_PLAY}
    unexplained = {ch: round(x, 2) for ch, x in unexplained.items() if x > 0.5}
    offline_total = ledger.get((vid, y, m, "offline_bookings"), 0.0)
    recorded_offline = plat.get("offline_bookings", 0.0)
    to_place = sum(unexplained.values())

    online = [(fs, b) for fs, b in items if b.medium == "online" and _revenue(b) > 0]
    share = defaultdict(float)
    for fs, b in online:
        share[fs] += _revenue(b)
    if not share:  # no online sales this month: use the venue's all-time online mix, else its courts
        for fs, b in venue_items:
            if b.medium == "online":
                share[fs] += _revenue(b)
    if not share:
        share = defaultdict(float, {s: float(n) for s, n in courts.items()})
    tot_share = sum(share.values()) or 1

    month_price = _price(online) if len(online) >= MIN_PRICE_SAMPLE else {}
    venue_price = _price(venue_items)
    imputed = defaultdict(float)
    imputed_h = unplaced_h = 0.0
    detail = {}
    for s, w in share.items():
        if s not in courts or not to_place:
            continue
        money = to_place * w / tot_share
        base = venue_price.get(s) or port_price.get(s)
        price = month_price.get(s)
        if not price or (base and not 0.5 * base <= price <= 2 * base):
            price = base  # too few or wallet-paid bookings this month: use the venue's usual price
        if not price:
            unplaced_h += 0
            detail[s] = {"money": round(money, 2), "price_per_hour": None, "hours": 0, "unplaced_hours": None}
            continue
        need = money / price * 60  # minutes
        weights = pattern.get(s) or pattern.get("*") or {}
        cells = [(d, h) for d in days for h in hours]
        wt = {c: weights.get((c[0].weekday(), c[1]), 0.0) for c in cells}
        if not sum(wt.values()):
            wt = {c: 1.0 for c in cells}
        free = {c: max(0.0, cap.get((c[0], c[1], s), 0) - rec.get((c[0], c[1], s), 0)) for c in cells}
        placed = _fill(need, wt, free)
        for c, x in placed.items():
            imputed[(c[0], c[1], s)] += x
        got = sum(placed.values())
        imputed_h += got / 60
        unplaced_h += (need - got) / 60
        detail[s] = {"money": round(money, 2), "price_per_hour": round(price, 2), "hours": round(got / 60, 1),
                     "unplaced_hours": round((need - got) / 60, 1)}

    keys = set(cap) | set(rec) | set(imputed)
    for d, h, s in keys:
        db.add(HistSlot(venue_id=vid, day=d, hour=h, sport=s, capacity_min=cap.get((d, h, s), 0.0),
                        recorded_min=round(rec.get((d, h, s), 0.0), 2), imputed_min=round(imputed.get((d, h, s), 0.0), 2)))
    cap_min = sum(cap.values())
    rec_min = sum(rec.values())
    over = sum(max(0.0, rec.get(k, 0) - cap.get(k, 0)) for k in rec)
    return {
        "bookings": len(items), "open_hours": f"{open_h:02d}:00-{close_h:02d}:00", "courts": courts,
        "compare": compare, "offline_sales_file": round(offline_total, 2), "offline_recorded": round(recorded_offline, 2),
        "offline_to_place": round(to_place, 2), "unexplained": unexplained, "imputed": detail, "imputed_hours": round(imputed_h, 2),
        "unplaced_hours": round(unplaced_h, 2),
        "occupancy_recorded": round(rec_min / cap_min, 4) if cap_min else None,
        "occupancy_total": round((rec_min + sum(imputed.values())) / cap_min, 4) if cap_min else None,
        "overbooked_hours": round(over / 60, 1),
    }


def _open_hours(items, keep: float = 0.98) -> tuple[int, int]:
    """Smallest hour range holding ``keep`` of the booked minutes, so a stray 6 AM booking doesn't widen it."""
    mins = defaultdict(float)
    for _, b in items:
        for h, x in _hour_split(b.start_min, b.end_min):
            mins[h] += x
    total = sum(mins.values())
    lo, hi = min(mins), max(mins)
    trim = total * (1 - keep)
    while lo < hi and mins.get(lo, 0) + mins.get(hi, 0) and trim > 0:
        side = lo if mins.get(lo, 0) <= mins.get(hi, 0) else hi
        if mins.get(side, 0) > trim:
            break
        trim -= mins.get(side, 0)
        lo, hi = (lo + 1, hi) if side == lo else (lo, hi - 1)
    return lo, hi + 1


def _fill(need: float, weight: dict, free: dict) -> dict:
    """Spread ``need`` minutes over cells in proportion to ``weight``, never past each cell's ``free`` room."""
    placed = defaultdict(float)
    active = {c for c, w in weight.items() if w > 0 and free.get(c, 0) > 0}
    left = need
    for _ in range(50):
        if left <= 1e-6 or not active:
            break
        tw = sum(weight[c] for c in active)
        spill = 0.0
        for c in list(active):
            want = left * weight[c] / tw
            room = free[c] - placed[c]
            take = min(want, room)
            placed[c] += take
            spill += want - take
            if free[c] - placed[c] <= 1e-6:
                active.discard(c)
        left = spill
    if left > 1e-6:  # pattern cells are full: use any open cell with room, evenly
        rest = [c for c in free if free[c] - placed[c] > 1e-6]
        for _ in range(50):
            if left <= 1e-6 or not rest:
                break
            each = left / len(rest)
            left = 0.0
            for c in list(rest):
                take = min(each, free[c] - placed[c])
                placed[c] += take
                left += each - take
                if free[c] - placed[c] <= 1e-6:
                    rest.remove(c)
    return {c: x for c, x in placed.items() if x > 0}


def write_report(db, path, out, files):
    venues = {v.id: v for v in db.scalars(select(Venue))}
    L = ["# Platform bookings import — report", "",
         f"Bookings imported: {out['bookings']} · venue-months: {out['venue_months']} · imputed offline hours: "
         f"{out['imputed_hours']} · hours that did not fit court capacity: {out['unplaced_hours']}", "",
         "Revenue is unchanged: the sales files and trackers remain the source of every rupee. Gaps below are for review.", "",
         "## Files", "", "| File | Platform | Bookings | Status |", "|---|---|---:|---|"]
    for f in files:
        L.append(f"| {f['file']} | {f['platform'] or ''} | {f['bookings']} | {f['status']} |")
    L += ["", "## Rows not imported", ""] + [f"- {k}: {v}" for k, v in sorted(out["skipped"].items())]
    if out["sport_guessed"]:
        L += ["", "Sport not stated or not a court the venue has (assigned to the venue's main sport): " +
              ", ".join(f"{k} {v}" for k, v in sorted(out["sport_guessed"].items()))]
    L += ["", "## Venue-months", "",
          "| Venue | Month | Bookings | Hours open | Occupancy (recorded) | Occupancy (with offline) | Offline in sales file | "
          "Offline recorded | Offline imputed (₹) | Imputed h | Unplaced h | Platform gaps (export − sales file) |",
          "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for a in db.scalars(select(HistBookingMonth).order_by(HistBookingMonth.venue_id, HistBookingMonth.year,
                                                          HistBookingMonth.month)):
        d = a.data
        gaps = "; ".join(f"{ch} {g['gap']:+,.0f}" for ch, g in d["compare"].items() if abs(g["gap"]) >= 1)
        pct = lambda x: "" if x is None else f"{x * 100:.1f}%"  # noqa: E731
        L.append(f"| {venues[a.venue_id].code} | {a.year}-{a.month:02d} | {d['bookings']} | {d['open_hours']} | "
                 f"{pct(d['occupancy_recorded'])} | {pct(d['occupancy_total'])} | {d['offline_sales_file']:,.0f} | "
                 f"{d['offline_recorded']:,.0f} | {d['offline_to_place']:,.0f} | {d['imputed_hours']:.1f} | "
                 f"{d['unplaced_hours']:.1f} | {gaps} |")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--report", default="bookings_report.md")
    a = ap.parse_args()
    out = run(a.folder, a.report)
    print({k: v for k, v in out.items() if k not in ("skipped",)})
    print(f"Report written to {a.report}")


if __name__ == "__main__":
    main()
