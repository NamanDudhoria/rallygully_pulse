"""Parsers for the sales workbooks.

Summary sheets give the authoritative monthly figure for each revenue channel. Detail sheets
(daily offline takings, platform settlements, member / event / brand lists) give when, which sport
and who — they are used only when they add up to the Summary figure (see ingest.reconcile).
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from .sources import MONTHS, month_bounds

SPORTS = {"pickleball": "Pickleball", "turf": "Turf", "padel": "Padel", "football": "Football",
          "cricket": "Cricket", "basketball": "Basketball", "box cricket": "Cricket"}

# Summary row label → canonical channel. Parent rows (Offline, Online, Community, Pay & Play, B2B,
# Commerce, …) are subtotals of these and are never counted.
CHANNELS = {
    "district": "district", "hudle": "hudle", "playo": "playo", "khelomore": "khelomore", "zplys": "zplys",
    "bookings": "offline_bookings", "memberships": "memberships",
    "rallygully community games": "pickup_games", "pickup games": "pickup_games",
    "social events/tournaments": "social_events",
    "community partnerships": "community_partnerships", "community partnership": "community_partnerships",
    "courtly (organized by rallygully)": "courtly", "courtly": "courtly",
    "sportomic (organized by rallygully)": "sportomic", "sportomic": "sportomic",
    "misfits community": "misfits",
    "academy": "academy", "coaching": "coaching",
    "corporate events": "corporate_events", "all b2b partnerships": "b2b_partnerships",
    "brand activation": "brand_activation",
    "rentals": "product_rental", "product rental": "product_rental", "product sales": "product_sales",
    "f&b": "fnb",
    "other income": "other_income", "other income ( football management)": "other_income",
}
PARENTS = {"offline", "online: third party platform", "online", "community", "pay & play", "b2b",
           "events & partnership", "commerce"}
VERTICAL = {
    "district": "online", "hudle": "online", "playo": "online", "khelomore": "online", "zplys": "online",
    "offline_bookings": "offline", "memberships": "offline",
    "pickup_games": "community", "social_events": "community", "community_partnerships": "community",
    "courtly": "community", "sportomic": "community", "misfits": "community",
    "academy": "academy", "coaching": "academy",
    "corporate_events": "b2b", "b2b_partnerships": "b2b", "brand_activation": "b2b",
    "product_rental": "commerce", "product_sales": "commerce", "fnb": "commerce",
    "other_income": "other",
}


def norm(v) -> str:
    return re.sub(r"\s+", " ", str(v).strip().lower()) if v not in (None, "") else ""


def num(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("₹", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def is_error(v) -> bool:
    return isinstance(v, str) and v.strip().startswith("#")


# ── Dates ────────────────────────────────────────────────────────────────────
def date_range(v, y: int, m: int) -> tuple[date, date, str]:
    """A cell like '1 - 7 Apr', '28-29 Mar', '12,26 & 30 July', 'Aug', a datetime, or free text →
    (from, to, basis). Anything without a usable day becomes the whole accounting month."""
    lo, hi = month_bounds(y, m)
    if isinstance(v, datetime):
        return v.date(), v.date(), "day"
    if isinstance(v, date):
        return v, v, "day"
    s = str(v or "")
    words = re.findall(r"[A-Za-z]+", s)
    months = [MONTHS[w.lower()] for w in words if w.lower() in MONTHS]
    days = [int(d) for d in re.findall(r"(\d{1,2})(?:st|nd|rd|th)?\b", s) if 1 <= int(d) <= 31]
    if not days:
        if months and months[0] != m:
            return lo, hi, "month"  # a different month named with no day — keep in the accounting month
        return lo, hi, "month"
    def mk(mon, d):
        yr = y - 1 if mon > m + 6 else y + 1 if mon < m - 6 else y
        try:
            return date(yr, mon, d)
        except ValueError:
            return None
    m1 = months[0] if months else m
    m2 = months[-1] if months else m
    a, b = mk(m1, min(days) if len(months) < 2 else days[0]), mk(m2, max(days) if len(months) < 2 else days[-1])
    if not a or not b:
        return lo, hi, "month"
    if b < a:
        a, b = b, a
    return a, b, "day" if a == b else "period"


# ── Summary ──────────────────────────────────────────────────────────────────
@dataclass
class Summary:
    period: str | None
    sport_cols: list[str]            # sports split out in the Summary, e.g. ['Pickleball', 'Turf', 'Padel']
    single_sport: str | None         # the one sport a single-column Summary is labelled with
    leaves: dict = field(default_factory=dict)   # channel -> {"total": x, "sports": {sport: x}, "row": n, "label": s}
    total: float | None = None
    total_error: bool = False
    pnl: list = field(default_factory=list)      # (label, amount, note)
    errors: list = field(default_factory=list)   # (label, raw)


def parse_summary(ws) -> Summary:
    rows = [[c.value for c in r] for r in ws.iter_rows()]
    cands = [(j, i) for i, r in enumerate(rows) for j, v in enumerate(r) if norm(v) == "particulars"]
    pc, hr = min(cands)  # the main table is the left-most "Particulars" (some files carry a helper table)
    period = next((str(v) for r in rows[:hr] for v in r[pc:pc + 3] if v not in (None, "")), None)
    head = {j: norm(rows[hr][j]) for j in range(pc + 1, min(len(rows[hr]), pc + 6)) if norm(rows[hr][j])}
    sub = {}
    if hr + 1 < len(rows):
        sub = {j: norm(rows[hr + 1][j]) for j in range(pc + 1, min(len(rows[hr + 1]), pc + 6))
               if norm(rows[hr + 1][j]) and norm(rows[hr + 1][pc]) == ""}
    cols = sorted(set(head) | set(sub))
    if not cols:
        cols = [pc + 1]
    lab_of = {j: sub.get(j) or head.get(j) for j in cols}
    # The total column is the one labelled 'Total' (some files put it last), else the first.
    total_col = next((j for j in cols if lab_of[j] == "total"), None)
    if total_col is None:
        total_col = next((j for j in cols if lab_of[j] not in SPORTS), cols[0])
    sport_cols = {j: SPORTS[lab_of[j]] for j in cols if j != total_col and lab_of[j] in SPORTS}
    single = None
    if not sport_cols:
        single = SPORTS.get(lab_of[total_col])
    elif total_col == cols[0] and lab_of[total_col] in SPORTS and len(cols) == 1:
        single, sport_cols = SPORTS[lab_of[total_col]], {}
    if lab_of[total_col] in SPORTS and sport_cols:
        # No separate total column: the first sport column was taken as the total. Treat all as sports.
        sport_cols = {j: SPORTS[lab_of[j]] for j in cols if lab_of[j] in SPORTS}
        total_col = None
    s = Summary(period, list(sport_cols.values()), single)
    after_total = False
    for i, r in enumerate(rows[hr + 1:], start=hr + 2):
        lab_raw = r[pc] if pc < len(r) else None
        lab = norm(lab_raw)
        if not lab:
            continue
        if total_col is None:  # sports only: total = sum of sport columns
            xs = [r[j] for j in sport_cols if j < len(r)]
            v = next((x for x in xs if is_error(x)), None) or sum(num(x) or 0 for x in xs)
        else:
            v = r[total_col] if total_col < len(r) else None
        if lab.startswith("total gross"):
            s.total, s.total_error, after_total = num(v), is_error(v), True
            continue
        if after_total:
            tc = total_col if total_col is not None else max(sport_cols)
            note = next((str(x) for x in r[tc + 1:tc + 4] if isinstance(x, str) and x.strip()), None)
            s.pnl.append((str(lab_raw).strip(), num(v), note))
            continue
        if lab in PARENTS:
            continue
        ch = CHANNELS.get(lab)
        if ch is None:
            s.errors.append((str(lab_raw), v))
            continue
        if is_error(v):
            s.errors.append((str(lab_raw), v))
            s.leaves.setdefault(ch, {"total": None, "sports": {}, "row": i, "label": str(lab_raw).strip(), "error": True})
            continue
        leaf = s.leaves.setdefault(ch, {"total": 0.0, "sports": {}, "row": i, "label": str(lab_raw).strip()})
        leaf["total"] = (leaf["total"] or 0) + (num(v) or 0)
        for j, sp in sport_cols.items():
            x = num(r[j]) if j < len(r) else None
            if x:
                leaf["sports"][sp] = leaf["sports"].get(sp, 0) + x
    return s


# ── Detail lines ─────────────────────────────────────────────────────────────
@dataclass
class Line:
    channel: str
    gross: float
    sheet: str
    row: int
    date_from: date
    date_to: date
    basis: str
    sport: str | None = None
    commission: float | None = None
    gateway: float | None = None
    other_costs: float | None = None
    tds: float | None = None
    tcs: float | None = None
    net: float | None = None
    hours: float | None = None
    people: int | None = None
    counterparty: str | None = None
    description: str | None = None


def _sport_in(text: str) -> str | None:
    t = norm(text)
    for k in sorted(SPORTS, key=len, reverse=True):
        if k in t:
            return SPORTS[k]
    if "paddle" in t:  # pickleball paddles
        return "Pickleball"
    return None


# Daily offline sheet columns → (channel | role, sport)
def _daily_role(label: str) -> tuple[str, str | None] | None:
    l = norm(label)
    if not l or l in ("day", "discount", "discount given"):
        return None
    if l == "date":
        return ("date", None)
    if "hour" in l:
        return ("hours", None)
    if l.startswith("total"):
        return ("row_total", None)
    if "payment gateway" in l:
        return ("gateway", None)
    if l.startswith("net revenue"):
        return ("net", None)
    if "partnership" in l:
        return ("community_partnerships", _sport_in(l))
    if l.startswith("community"):
        return ("pickup_games", _sport_in(l.replace("community", "")))
    if "rental" in l:
        return ("product_rental", "Padel" if "padel" in l else "Pickleball")
    if l in ("f&b", "drinks revenue", "energy drinks"):
        return ("fnb", None)
    if l.startswith("booking revenue") or l.startswith("revenue"):
        return ("offline_bookings", _sport_in(l.replace("booking revenue", "")))
    if l in SPORTS:
        return ("offline_bookings", SPORTS[l])
    return ("unknown", None)


def parse_daily(ws, y: int, m: int) -> tuple[list[Line], list[str]]:
    """The 'Offline & RG Community' / 'Offline Bookings' sheet: one row per day."""
    rows = [[c.value for c in r] for r in ws.iter_rows()]
    rows = [(i, r) for i, r in enumerate(rows, start=1) if any(v not in (None, "") for v in r)]
    notes = []
    if not rows:
        return [], notes
    (hi, head), rest = rows[0], rows[1:]
    labels = [norm(v) for v in head]
    if rest and rest[0][1] and rest[0][1][0] in (None, "") and any(isinstance(v, str) and v.strip() for v in rest[0][1]):
        subrow = rest[0][1]  # two-row header: sports under 'Booking Revenue'
        labels = [norm(subrow[j]) if j < len(subrow) and norm(subrow[j]) else labels[j] for j in range(len(labels))]
        rest = rest[1:]
    roles = {j: _daily_role(lab) for j, lab in enumerate(labels)}
    unknown = [labels[j] for j, r in roles.items() if r and r[0] == "unknown"]
    if unknown:
        notes.append(f"unrecognised daily columns ignored: {unknown}")
    lo, hi_d = month_bounds(y, m)
    out, outside = [], []
    for i, r in rest:
        first = norm(r[0]) if r else ""
        if first.startswith("total"):
            break
        d = r[0] if r else None
        if isinstance(d, datetime):
            day = d.date()
        else:
            f, t, basis = date_range(d, y, m)
            if basis != "day":
                notes.append(f"row {i}: '{d}' is not a date — row skipped")
                continue
            day = f
        if not (lo <= day <= hi_d):
            amounts = [num(r[j]) for j, ro in roles.items() if ro and ro[0] not in ("date", "hours") and j < len(r)]
            if any(a for a in amounts if a):
                outside.append(day)
            continue
        hours = next((num(r[j]) for j, ro in roles.items() if ro and ro[0] == "hours" and j < len(r)), None)
        gw = next((num(r[j]) for j, ro in roles.items() if ro and ro[0] == "gateway" and j < len(r)), None)
        day_lines = []
        for j, ro in roles.items():
            if not ro or ro[0] in ("date", "hours", "row_total", "gateway", "net", "unknown") or j >= len(r):
                continue
            x = num(r[j])
            if x:
                day_lines.append(Line(ro[0], x, ws.title, i, day, day, "day", sport=ro[1]))
        # attach hours to bookings, spread the day's gateway fee pro-rata
        tot = sum(l.gross for l in day_lines)
        for l in day_lines:
            if gw and tot:
                l.gateway = round(gw * l.gross / tot, 4)
        books = [l for l in day_lines if l.channel == "offline_bookings"]
        if hours and books:
            share = sum(b.gross for b in books)
            for b in books:
                b.hours = round(hours * b.gross / share, 4) if share else None
        out.extend(day_lines)
    if outside:
        notes.append(f"OUTSIDE:{len(outside)} rows with takings are dated outside the month "
                     f"({min(outside)} – {max(outside)}) — excluded")
    return out, notes


# Settlement / list sheets ────────────────────────────────────────────────────
def _col_roles(labels: list[str]) -> dict:
    roles = {}
    for j, l in enumerate(labels):
        if not l:
            continue
        if l in ("link", "link ", "s.no.", "sno.", "sno", "day"):
            continue
        if l.startswith("date") or l in ("month", "start month", "start date"):
            roles.setdefault("date", j)
        elif l.startswith("paid to brand"):
            roles["other_costs"] = j
        elif l.startswith("paid to") or l in ("amt", "amount collected", "amount (₹)", "misfits gross", "f&b"):
            roles.setdefault("gross", j)
        elif l == "amount":
            roles.setdefault("amount", j)
        elif l.startswith("net revenue (post comm") or l.startswith("net revenue (misfits"):
            roles["net"] = j
        elif "comission" in l or "commission" in l or l.startswith("commisions"):
            roles["commission" if "gst" not in l or "including" in l else "gst"] = j
        elif l.startswith("gst"):
            roles["gst"] = j
        elif "payment gateway" in l:
            roles["gateway"] = j
        elif l.startswith("event") or "event cost" in l:
            roles["event_costs"] = j
        elif l.startswith("net revenue") or l == "net":
            roles["net"] = j
        elif l.startswith("tds"):
            roles["tds"] = j
        elif l.startswith("tcs"):
            roles["tcs"] = j
        elif l.startswith("final") or l == "net cash received":
            roles["final"] = j
        elif "margin" in l:
            roles["margin"] = j
        elif l in ("number of people",):
            roles["people"] = j
        elif l in ("member name", "name", "name of brand", "name of brand/person", "brand name", "brand", "organisation", "coaching"):
            roles.setdefault("party", j)
        elif l in ("sport", "sports", "sports type"):
            roles["sport"] = j
        elif l in ("type", "membership type", "coaching type", "partnership type", "type of collab", "deliverables",
                   "timings", "notes", "poc", "venue", "status", "credits issued"):
            roles.setdefault("desc", []).append(j)
    if "gross" not in roles and "amount" in roles:
        roles["gross"] = roles.pop("amount")      # e.g. brand sheets: 'Amount' is the gross
    elif "amount" in roles:
        roles["final"] = roles.pop("amount")      # Hudle: 'Paid to Hudle' … 'Amount' = what reached us
    return roles


def _looks_like_header(cells: list[str]) -> bool:
    words = [c for c in cells if c]
    return len(words) >= 2 and all(num(c) is None for c in words) and any(
        c in ("amt", "amount", "amount collected") or c.startswith("paid to") for c in words)


def parse_list(ws, channel: str, y: int, m: int) -> tuple[list[Line], dict, list[str]]:
    """A settlement or list sheet → lines, plus any sport split written under the total."""
    rows = [(i, [c.value for c in r]) for i, r in enumerate(ws.iter_rows(), start=1)]
    rows = [(i, r) for i, r in rows if any(v not in (None, "") for v in r)]
    notes, lines, split = [], [], {}
    if not rows:
        return lines, split, notes
    (_, head), rest = rows[0], rows[1:]
    labels = [norm(v) for v in head]
    roles = _col_roles(labels)
    if "gross" not in roles:
        if "final" in roles:
            roles["gross"] = roles.pop("final")   # Sportomic sheets that only state the amount received
        else:
            notes.append(f"no amount column in {labels}")
            return lines, split, notes
    g = roles["gross"]
    running = 0.0
    done = False
    for i, r in rest:
        cells = [norm(v) for v in r]
        if any(c == "total" or c.startswith("total ") for c in cells[:3]):
            done = True
            continue
        if done and _looks_like_header(cells):
            # a second table under the first (e.g. 'Offline DUPR' under the Courtly settlements)
            roles = _col_roles(cells)
            if "gross" not in roles:
                continue
            g, running, done = roles["gross"], 0.0, False
            continue
        x = num(r[g]) if g < len(r) else None
        if done:
            lab = next((c for c in cells if c), "")
            if lab in SPORTS and x is None:
                x = next((num(v) for v in r[1:] if num(v) is not None), None)
            if lab in SPORTS and x is not None:
                split[SPORTS[lab]] = split.get(SPORTS[lab], 0) + x
            elif any(v not in (None, "") for v in r):
                notes.append(f"row {i} after the total ignored: {[v for v in r if v not in (None, '')][:6]}")
            continue
        has_text = any(isinstance(r[j], (str, datetime, date)) and str(r[j]).strip()
                       for j in [roles.get("date"), roles.get("party"), roles.get("sport"), *roles.get("desc", [])]
                       if j is not None and j < len(r))
        if x is None or (not has_text and abs(x - running) < 0.01 and running):
            if x is not None and not has_text:
                done = True  # an unlabelled total row
            continue
        if not x:
            continue  # nil rows (no sales that day, nil settlement)
        f, t, basis = date_range(r[roles["date"]] if "date" in roles and roles["date"] < len(r) else None, y, m)
        get = lambda k: num(r[roles[k]]) if k in roles and roles[k] < len(r) else None  # noqa: E731
        comm = (get("commission") or 0) + (get("gst") or 0) if (get("commission") or get("gst")) else None
        costs = (get("event_costs") or 0) + (get("other_costs") or 0) if (get("event_costs") or get("other_costs")) else None
        net = get("final") if get("final") is not None else get("net")
        if net is None and get("margin") is not None:
            net = get("margin") - (get("gateway") or 0)
        desc = " · ".join(str(r[j]).strip() for j in roles.get("desc", []) if j < len(r) and r[j] not in (None, ""))
        sport = _sport_in(str(r[roles["sport"]])) if "sport" in roles and roles["sport"] < len(r) and r[roles["sport"]] else None
        party = str(r[roles["party"]]).strip() if "party" in roles and roles["party"] < len(r) and r[roles["party"]] else None
        date_txt = str(r[roles["date"]]).strip() if "date" in roles and roles["date"] < len(r) and r[roles["date"]] not in (None, "") and basis != "day" else None
        if date_txt:
            desc = f"{desc} · when: {date_txt}" if desc else f"when: {date_txt}"
        ppl = get("people")
        lines.append(Line(channel, x, ws.title, i, f, t, basis, sport=sport, commission=comm, gateway=get("gateway"),
                          other_costs=costs, tds=get("tds"), tcs=get("tcs"), net=net,
                          people=int(ppl) if ppl else None, counterparty=party, description=desc or None))
        running += x
    return lines, split, notes
