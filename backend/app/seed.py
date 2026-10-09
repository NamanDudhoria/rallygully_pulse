"""Demo data: 3 venues, ~100 days of operating history, and a live 'today'.

    python -m app.seed --reset

History is generated directly into the tables (fast) but follows the same rules the
services enforce: one active allocation per facility-slot, payments only for paid
activity, District no-shows release inventory, etc.
"""
import argparse
import random
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from .auth import hash_password
from .db import Base, SessionLocal, engine
from .models import (AcademyOccurrence, AcademyReconciliation, AcademySeries, Allocation, Booking, CommunityGame,
                     CorporateEvent, Customer, DayClose, Facility, Notification, OperationalBlock, Participant,
                     Payment, PricingRule, Refund, User, Venue, VmAssignment, WeeklyHours)
from .services.pricing import PriceBook
from .timeutil import at_minute, minute_of, now_local

HISTORY_DAYS = 100
R = random.Random(42)
LINK = "https://chat.whatsapp.com/rallygully-{}"

FIRST = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Ayaan", "Krishna", "Ishaan", "Ananya",
         "Diya", "Aadhya", "Saanvi", "Myra", "Kiara", "Ira", "Riya", "Kabir", "Rohan", "Neha", "Pooja", "Rahul",
         "Karan", "Simran", "Tanvi", "Meera", "Nikhil", "Varun", "Aditi", "Shreya", "Dev", "Aman", "Ritika", "Sneha",
         "Yash", "Tara", "Zoya", "Raghav", "Naina", "Kunal", "Isha", "Arnav", "Avni", "Harsh", "Mehak"]
LAST = ["Sharma", "Verma", "Gupta", "Mehta", "Kapoor", "Malhotra", "Singh", "Chopra", "Bansal", "Agarwal", "Jain",
        "Khanna", "Arora", "Bhatia", "Sethi", "Nair", "Iyer", "Reddy", "Rao", "Das", "Bose", "Saxena", "Mittal"]

VENUES = [
    {"code": "EOK", "name": "RallyGully East of Kailash", "location": "East of Kailash, New Delhi",
     "hours": {0: (17, 23), 1: (17, 23), 2: (17, 23), 3: (17, 23), 4: (17, 23), 5: (8, 23), 6: (8, 22)},
     "facilities": [("Court 1", "Court", "Pickleball"), ("Court 2", "Court", "Pickleball"), ("Court 3", "Court", "Padel")],
     "base": 0.62, "growth": 0.10},
    {"code": "SKT", "name": "RallyGully Saket", "location": "Saket, New Delhi",
     "hours": {d: (6, 22) for d in range(5)} | {5: (6, 23), 6: (6, 23)},
     "facilities": [("Court A", "Court", "Badminton"), ("Court B", "Court", "Badminton"), ("Court C", "Court", "Badminton"),
                    ("Court D", "Court", "Badminton"), ("Court E", "Court", "Pickleball")],
     "base": 0.48, "growth": 0.18},
    {"code": "GGN", "name": "RallyGully Gurugram 54", "location": "Sector 54, Gurugram",
     "hours": {d: (6, 23) for d in range(7)},
     "facilities": [("Turf 1", "Turf", "Football 6v6"), ("Turf 2", "Turf", "Football 6v6"), ("Box", "Turf", "Box Cricket")],
     "base": 0.42, "growth": 0.05},
]

# (sport, weekdays, start_h, end_h, 60-min price, 30-min price)
PRICES = [
    ("Pickleball", "01234", 6, 17, 600, 350), ("Pickleball", "01234", 17, 24, 800, 450),
    ("Pickleball", "56", 6, 24, 900, 500),
    ("Padel", "01234", 6, 24, 1600, 900), ("Padel", "56", 6, 24, 1800, 1000),
    ("Badminton", "01234", 6, 17, 400, 220), ("Badminton", "01234", 17, 24, 500, 280),
    ("Badminton", "56", 6, 24, 550, 300),
    ("Football 6v6", "01234", 6, 18, 1500, 800), ("Football 6v6", "01234", 18, 24, 2200, 1200),
    ("Football 6v6", "56", 6, 24, 2400, 1300),
    ("Box Cricket", "0123456", 6, 24, 1400, 750),
]


def demand_curve(hour: int, weekend: bool) -> float:
    if weekend:
        return {6: .45, 7: .75, 8: .85, 9: .8, 10: .6, 11: .45, 12: .3, 13: .25, 14: .25, 15: .3, 16: .5,
                17: .75, 18: .9, 19: .95, 20: .9, 21: .75, 22: .45}.get(hour, .3)
    return {6: .55, 7: .6, 8: .35, 9: .2, 10: .15, 11: .12, 12: .12, 13: .12, 14: .12, 15: .18, 16: .3,
            17: .6, 18: .85, 19: .97, 20: .95, 21: .8, 22: .45}.get(hour, .2)


def phone() -> str:
    return R.choice("6789") + "".join(R.choice("0123456789") for _ in range(9))


def run(reset: bool) -> None:
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    if db.query(Venue).count():
        print("Database already seeded (use --reset to rebuild).")
        return
    now = now_local()
    today = now.date()
    start = today - timedelta(days=HISTORY_DAYS)

    # ── venues, facilities, hours, pricing, users ──
    venues, facs = [], {}
    for i, spec in enumerate(VENUES):
        v = Venue(code=spec["code"], name=spec["name"], location=spec["location"])
        db.add(v)
        db.flush()
        for wd, (o, c) in spec["hours"].items():
            db.add(WeeklyHours(venue_id=v.id, weekday=wd, open_min=o * 60, close_min=c * 60))
        facs[v.id] = []
        for j, (name, ftype, sport) in enumerate(spec["facilities"]):
            f = Facility(venue_id=v.id, name=name, facility_type=ftype, sport=sport, sort_order=j)
            db.add(f)
            facs[v.id].append(f)
        sports = {s for _, _, s in spec["facilities"]}
        for sport, wds, sh, eh, p60, p30 in PRICES:
            if sport in sports:
                eff = start
                if spec["code"] == "EOK" and sport == "Pickleball" and sh == 17:
                    # Price versioning demo: evening pickleball moved ₹700 → ₹800 45 days ago.
                    db.add(PricingRule(venue_id=v.id, sport=sport, weekdays=wds, start_min=sh * 60, end_min=eh * 60,
                                       duration_min=60, amount=700, effective_from=start))
                    db.add(PricingRule(venue_id=v.id, sport=sport, weekdays=wds, start_min=sh * 60, end_min=eh * 60,
                                       duration_min=30, amount=400, effective_from=start))
                    eff = today - timedelta(days=45)
                db.add(PricingRule(venue_id=v.id, sport=sport, weekdays=wds, start_min=sh * 60, end_min=eh * 60,
                                   duration_min=60, amount=p60, effective_from=eff))
                db.add(PricingRule(venue_id=v.id, sport=sport, weekdays=wds, start_min=sh * 60, end_min=eh * 60,
                                   duration_min=30, amount=p30, effective_from=eff))
        venues.append((v, spec))
    db.flush()

    pw = hash_password("pulse1234")
    db.add(User(name="HQ Operations", email="hq@rallygully.com", password_hash=pw, role="hq"))
    vm_names = {"EOK": "Rohit Malhotra", "SKT": "Priya Nair", "GGN": "Aman Bhatia"}
    for v, spec in venues:
        u = User(name=vm_names[spec["code"]], email=f"vm.{spec['code'].lower()}@rallygully.com", password_hash=pw, role="vm")
        u.venues = [VmAssignment(venue_id=v.id)]
        db.add(u)
    db.flush()
    vm_ids = {v.id: db.query(User).filter(User.email == f"vm.{spec['code'].lower()}@rallygully.com").one().id
              for v, spec in venues}

    # ── customers (heavy-tailed repeat behaviour) ──
    pool = []
    used = set()
    for _ in range(1600):
        p = phone()
        while p in used:
            p = phone()
        used.add(p)
        pool.append(Customer(phone=p, created_at=datetime.combine(start, datetime.min.time())))
    db.add_all(pool)
    db.flush()
    weights = [1 / (i + 1) ** 0.8 for i in range(len(pool))]

    def pick_customer() -> Customer:
        return R.choices(pool, weights)[0]

    def name_customer(c: Customer):
        if not c.name:
            c.name = f"{R.choice(FIRST)} {R.choice(LAST)}"
            c.name_locked = True

    book = PriceBook.load(db, [v.id for v, _ in venues])

    # ── recurring academy series (materialized occurrences) ──
    academy_specs = []
    for v, spec in venues:
        if spec["code"] == "SKT":
            academy_specs.append((v, "Shuttle Stars Academy", [facs[v.id][2].id, facs[v.id][3].id], "01234", 6 * 60, 8 * 60, 1200))
        if spec["code"] == "GGN":
            academy_specs.append((v, "Kickstart Football Academy", [facs[v.id][1].id], "56", 7 * 60, 9 * 60, 3000))
        if spec["code"] == "EOK":
            academy_specs.append((v, "Pickle Pros Juniors", [facs[v.id][0].id], "5", 8 * 60, 10 * 60, 1400))
    series_rows = []
    for v, name, fids, wds, s, e, val in academy_specs:
        sr = AcademySeries(venue_id=v.id, name=name, facility_ids=fids, recurring=True, weekdays=wds,
                           start_date=start, end_date=today + timedelta(days=60), start_min=s, end_min=e,
                           value_per_occurrence=val)
        db.add(sr)
        series_rows.append(sr)
    db.flush()

    allocs: list[Allocation] = []
    taken: set = set()  # (facility_id, date, slot)

    def take(v_id, fid, d, s, e, kind, ref, active=True, released=None):
        allocs.append(Allocation(venue_id=v_id, facility_id=fid, date=d, start_min=s, end_min=e, kind=kind,
                                 ref_id=ref, active=active, released_at=released,
                                 created_at=at_minute(d, s) - timedelta(hours=R.randint(2, 72))))
        if active:
            for slot in range(s, e, 30):
                taken.add((fid, d, slot))

    def free(fid, d, s, e):
        return all((fid, d, slot) not in taken for slot in range(s, e, 30))

    for sr in series_rows:
        d = sr.start_date
        while d <= sr.end_date:
            if str(d.weekday()) in sr.weekdays:
                o = AcademyOccurrence(series_id=sr.id, venue_id=sr.venue_id, date=d, start_min=sr.start_min,
                                      end_min=sr.end_min, value=sr.value_per_occurrence)
                db.add(o)
                db.flush()
                for fid in sr.facility_ids:
                    take(sr.venue_id, fid, d, sr.start_min, sr.end_min, "academy", o.id)
            d += timedelta(days=1)
        m = date(start.year, start.month, 1)
        while m < date(today.year, today.month, 1):
            month = m.strftime("%Y-%m")
            n_occ = sum(1 for o in sr.occurrences if o.date.strftime("%Y-%m") == month)
            if n_occ:
                db.add(AcademyReconciliation(series_id=sr.id, month=month,
                                             amount_collected=round(n_occ * sr.value_per_occurrence * R.uniform(.88, 1.0), -2),
                                             payment_method=R.choice(["upi", "other"])))
            m = (m + timedelta(days=32)).replace(day=1)

    bookings: list[Booking] = []
    payments: list[Payment] = []
    ext = 880000

    def hours_of(spec, d):
        o, c = spec["hours"][d.weekday()]
        return o * 60, c * 60

    for offset in range(HISTORY_DAYS + 8):
        d = start + timedelta(days=offset)
        is_future = d > today
        for v, spec in venues:
            o_min, c_min = hours_of(spec, d)
            weekend = d.weekday() >= 5
            trend = 1 + spec["growth"] * offset / HISTORY_DAYS
            day_noise = R.gauss(1, .08)

            # Anomaly demo: EOK pickleball demand collapsed yesterday (Court 2 mostly empty).
            eok_dip = spec["code"] == "EOK" and d == today - timedelta(days=1)

            # Community games
            game_slots = []
            if spec["code"] == "EOK" and d.weekday() in (1, 3):
                game_slots.append(("Pickleball Social — Intermediate", "Pickleball", [0, 1], 19 * 60, 21 * 60, 16, 400))
            if spec["code"] == "SKT" and d.weekday() == 5:
                game_slots.append(("Saturday Smash", "Badminton", [0, 1], 7 * 60, 9 * 60, 12, 300))
            if spec["code"] == "GGN" and d.weekday() == 2:
                game_slots.append(("Wednesday 6v6 Night", "Football 6v6", [0], 20 * 60, 22 * 60, 14, 350))
            for title, sport, idx, s, e, cap, fee in game_slots:
                fids = [facs[v.id][i].id for i in idx]
                if not all(free(f, d, s, e) for f in fids):
                    continue
                g = CommunityGame(venue_id=v.id, title=title, sport=sport, date=d, start_min=s, end_min=e, capacity=cap,
                                  per_person=fee, community_link=LINK.format(spec["code"].lower()),
                                  created_at=at_minute(d, s) - timedelta(days=3))
                cancelled = not is_future and R.random() < .04
                if cancelled:
                    g.status, g.cancel_reason = "cancelled", "low_registrations"
                db.add(g)
                db.flush()
                for f in fids:
                    take(v.id, f, d, s, e, "community", g.id, active=not cancelled)
                game_end = at_minute(d, e)
                if cancelled or game_end > now and d >= today:
                    if d == today and not cancelled and at_minute(d, s) <= now:
                        n = R.randint(cap // 2, cap - 2)  # game in progress: some added, not yet resolved
                        for c in R.sample(pool[:500], n):
                            name_customer(c)
                            db.add(Participant(game_id=g.id, customer_id=c.id, level=R.choice(["beginner", "intermediate", "advanced"])))
                    continue
                n = max(4, min(cap, int(R.gauss(cap * .75 * trend, 2))))
                for c in R.sample(pool[:500], n):
                    name_customer(c)
                    att = "no_show" if R.random() < .06 else "attended"
                    p = Participant(game_id=g.id, customer_id=c.id, level=R.choice(["beginner", "intermediate", "advanced"]),
                                    attendance=att)
                    if att == "attended":
                        p.payment_status, p.amount = "paid", fee
                        p.payment_method = R.choices(["upi", "cash", "card"], [6, 3, 1])[0]
                    db.add(p)
                    db.flush()
                    if att == "attended":
                        payments.append(Payment(source_type="participant", source_id=p.id, venue_id=v.id,
                                                activity_date=d, amount=fee, method=p.payment_method,
                                                collected_at=at_minute(d, s) + timedelta(minutes=R.randint(0, 40))))

            # Corporate events (occasional, weekends at GGN / EOK)
            if not is_future and R.random() < (.08 if spec["code"] == "GGN" else .03) and weekend:
                s, e = 16 * 60, 19 * 60
                fids = [facs[v.id][0].id, facs[v.id][1].id]
                if all(free(f, d, s, e) for f in fids):
                    amt = R.choice([12000, 15000, 18000, 25000])
                    ev = CorporateEvent(venue_id=v.id, company=R.choice(["Zomato", "Paytm", "MakeMyTrip", "Deloitte India",
                                                                         "Airtel", "Nykaa", "Policybazaar"]) + " Offsite",
                                        contact_name=f"{R.choice(FIRST)} {R.choice(LAST)}", contact_phone=phone(), date=d,
                                        start_min=s, end_min=e, amount=amt, payment_status="paid", payment_method="other")
                    db.add(ev)
                    db.flush()
                    for f in fids:
                        take(v.id, f, d, s, e, "corporate", ev.id)
                    payments.append(Payment(source_type="event", source_id=ev.id, venue_id=v.id, activity_date=d,
                                            amount=amt, method="other", collected_at=at_minute(d, s) - timedelta(days=5)))

            # Operational blocks
            if R.random() < .05:
                f = R.choice(facs[v.id])
                s = R.choice(range(o_min, c_min - 120, 60))
                e = s + R.choice([60, 120])
                if free(f.id, d, s, e):
                    blk = OperationalBlock(venue_id=v.id, facility_id=f.id, date=d, start_min=s, end_min=e,
                                           reason=R.choice(["maintenance", "facility_problem"]), note="Net/lighting repair",
                                           created_by=vm_ids[v.id], created_at=at_minute(d, s) - timedelta(hours=1))
                    db.add(blk)
                    db.flush()
                    take(v.id, f.id, d, s, e, "block", blk.id)

            # Consumer bookings, hour by hour
            for f in facs[v.id]:
                slot = o_min
                while slot < c_min:
                    p = spec["base"] * demand_curve(slot // 60, weekend) * trend * day_noise / .62
                    if eok_dip and f.sport == "Pickleball":
                        p *= .2 if f.name == "Court 2" else .55
                    if is_future:
                        p *= .45 * max(0, 1 - (d - today).days / 8)
                    duration = 30 if R.random() < .12 or slot + 60 > c_min else 60
                    end = slot + duration
                    if not free(f.id, d, slot, end) or R.random() > min(p, .97):
                        slot += 30 if not free(f.id, d, slot, slot + 30) else duration
                        continue
                    source = "district" if R.random() < .58 else "direct"
                    c = pick_customer()
                    if source == "direct":
                        name_customer(c)
                    value = book.price(f, d, slot, duration, d - timedelta(days=1)) or 0
                    if source == "direct" and R.random() < .1:
                        value = round(value * .9, -1)  # on-ground discount
                    begin = at_minute(d, slot)
                    finish = at_minute(d, end)
                    created = begin - timedelta(hours=R.choice([1, 3, 6, 20, 48]))
                    if d == today and created > now:
                        created = now - timedelta(minutes=R.randint(5, 120))
                    b = Booking(source=source, customer_id=c.id, venue_id=v.id, date=d, start_min=slot, end_min=end,
                                booking_value=value, payment_window_end_min=end + 30, created_at=created, sport=f.sport)
                    if source == "district":
                        ext += R.randint(1, 9)
                        b.external_ref = f"DST-{ext}"
                    prepaid = source == "district" and R.random() < .8
                    b.payment_status = "paid" if prepaid else "to_collect"
                    if prepaid:
                        b.payment_method, b.collected_amount = "online", value
                    bookings.append(b)
                    db.add(b)
                    db.flush()
                    if prepaid:
                        payments.append(Payment(source_type="booking", source_id=b.id, venue_id=v.id, activity_date=d,
                                                amount=value, method="online", collected_at=created))
                    if c.created_at > created:
                        c.created_at = created
                    started = begin <= now
                    over = finish + timedelta(minutes=30) <= now
                    roll = R.random()
                    if not started:
                        # future / upcoming: District unassigned, Direct already on its court
                        if source == "direct":
                            b.facility_id = b.original_facility_id = f.id
                            b.assigned_at = created
                            take(v.id, f.id, d, slot, end, "direct", b.id)
                        else:
                            taken.update((f.id, d, s2) for s2 in range(slot, end, 30))  # keep demand realistic
                        slot = end
                        continue
                    if roll < .03 and not over:
                        pass  # will stay pending (in-progress, unresolved)
                    if roll < .025:
                        b.status, b.cancel_reason, b.cancelled_at = "cancelled", R.choice(
                            ["court_unavailable", "maintenance", "staff_error"]), created + timedelta(hours=1)
                        take(v.id, f.id, d, slot, end, source, b.id, active=False, released=b.cancelled_at)
                        if prepaid:
                            db.add(Refund(booking_id=b.id, amount=value, reason="Cancelled by RallyGully",
                                          processed_at=b.cancelled_at + timedelta(days=1)))
                    elif roll < .085 and (begin + timedelta(minutes=30) <= now):
                        b.attendance = "no_show"
                        b.facility_id = b.original_facility_id = f.id if source == "direct" else None
                        if source == "direct":
                            take(v.id, f.id, d, slot, end, source, b.id, active=False,
                                 released=begin + timedelta(minutes=30))
                    elif d == today and not over and R.random() < .35:
                        # live: arrived & on court, maybe not yet paid
                        b.facility_id = b.original_facility_id = f.id
                        b.assigned_at = begin - timedelta(minutes=R.randint(0, 10))
                        b.attendance = "attended"
                        take(v.id, f.id, d, slot, end, source, b.id)
                    elif d == today and not over:
                        if source == "direct":
                            b.facility_id = b.original_facility_id = f.id
                            take(v.id, f.id, d, slot, end, source, b.id)
                        else:
                            taken.update((f.id, d, s2) for s2 in range(slot, end, 30))
                    else:
                        b.attendance = "attended"
                        b.assigned_at = begin - timedelta(minutes=R.randint(0, 10))
                        moved = R.random() < .04
                        alt = [x for x in facs[v.id] if x.sport == f.sport and x.id != f.id and free(x.id, d, slot, end)]
                        if moved and alt and source == "direct":
                            b.original_facility_id = alt[0].id
                            take(v.id, alt[0].id, d, slot, end, source, b.id, active=False,
                                 released=begin + timedelta(minutes=10))
                        else:
                            b.original_facility_id = f.id
                        b.facility_id = f.id
                        take(v.id, f.id, d, slot, end, source, b.id)
                        if not prepaid:
                            method = R.choices(["cash", "upi", "card"], [5, 6, 1])[0]
                            if spec["code"] == "SKT" and d == today - timedelta(days=1):
                                method = "cash"  # anomaly demo: cash-heavy day
                            b.payment_status, b.payment_method, b.collected_amount = "paid", method, value
                            payments.append(Payment(source_type="booking", source_id=b.id, venue_id=v.id, activity_date=d,
                                                    amount=value, method=method, actor_id=vm_ids[v.id],
                                                    collected_at=begin + timedelta(minutes=R.randint(-25, 40))))
                    slot = end
        db.flush()

    # Overdue example today: an unpaid walk-in whose window has lapsed.
    for b in bookings:
        if b.date == today and b.payment_status == "to_collect" and b.attendance != "pending":
            if minute_of(now, b.date) > b.payment_window_end_min:
                b.payment_status = "overdue"

    db.add_all(allocs)
    db.add_all(payments)

    # Day closes: most days closed on time; two missed with escalation.
    for offset in range(HISTORY_DAYS):
        d = start + timedelta(days=offset)
        for v, _ in venues:
            if R.random() < .03:
                db.add(DayClose(venue_id=v.id, date=d, status="missed", escalated_at=at_minute(d, 23 * 60 + 30),
                                missing_items=[{"type": "booking_payment", "ref_id": None,
                                                "message": "Direct 21:00–22:00: payment not recorded"}]))
            else:
                db.add(DayClose(venue_id=v.id, date=d, status="closed", closed_by=vm_ids[v.id],
                                closed_at=at_minute(d, 23 * 60 + R.randint(0, 25))))

    for b in bookings[-40:]:
        db.add(Notification(channel="whatsapp", recipient=db.get(Customer, b.customer_id).phone, template="booking_created",
                            body="Your RallyGully booking is confirmed. Join the community: " + LINK.format("community"),
                            status="simulated", ref_type="booking", ref_id=b.id, created_at=b.created_at))

    # Unused customers shouldn't exist (a profile is created by an interaction).
    db.flush()
    db.commit()
    _prune_unused(db)
    print(f"Seeded {len(venues)} venues, {len(bookings)} bookings, {len(allocs)} allocations, "
          f"{len(payments)} payments over {HISTORY_DAYS} days.")
    print("Logins (password pulse1234): hq@rallygully.com, vm.eok@rallygully.com, vm.skt@rallygully.com, vm.ggn@rallygully.com")


def _prune_unused(db: Session) -> None:
    from sqlalchemy import text
    db.execute(text("DELETE FROM customers WHERE id NOT IN (SELECT customer_id FROM bookings) "
                    "AND id NOT IN (SELECT customer_id FROM participants)"))
    db.commit()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    run(ap.parse_args().reset)
