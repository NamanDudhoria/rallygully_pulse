"""Statistical anomaly detection against each venue's own history (PRD §55-56).

Method: robust (modified) z-score — Iglewicz & Hoaglin (1993):
    z = 0.6745 · (x − median) / MAD
flagged when |z| ≥ 3.5. Median/MAD resist the outliers that a mean/stdev baseline
would absorb. Baseline is the same weekday over the last 8 weeks (weekly demand
seasonality); with fewer than 4 such days it falls back to the trailing 28 days,
and with fewer than 7 of those the metric is reported as "insufficient history".
When MAD is 0 (very stable metric) we use the mean absolute deviation × 1.2533,
and for small-count metrics we also require a minimum absolute change so that
"1 vs 0 no-shows" is never an anomaly.
"""
from datetime import date, timedelta

from sqlalchemy.orm import Session

from . import analytics
from .analytics import METRICS

Z_THRESHOLD = 3.5

CHECKS = {
    # metric: (label, format, min_abs_change)
    "net_revenue": ("Net revenue", "inr", 500.0),
    "occupancy": ("Occupancy", "pct", 0.05),
    "utilization": ("Utilization", "pct", 0.05),
    "total_activity": ("Booking volume", "int", 3),
    "cash_share": ("Cash share of collections", "pct", 0.10),
    "day_repeat_rate": ("Repeat customer share", "pct", 0.10),
    "no_shows": ("No-shows", "int", 2),
    "cancellations": ("Cancellations", "int", 2),
}


def _quantile(vals: list[float], q: float) -> float:
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def robust_z(x: float, history: list[float]) -> float | None:
    med = _quantile(history, 0.5)
    mad = _quantile([abs(v - med) for v in history], 0.5)
    if mad > 0:
        return 0.6745 * (x - med) / mad
    meanad = sum(abs(v - med) for v in history) / len(history)
    if meanad > 0:
        return (x - med) / (1.2533 * meanad)
    return None if x == med else float("inf") if x > med else float("-inf")


def _baseline(series: dict, d: date, f) -> tuple[list[float], str]:
    same = [d - timedelta(days=7 * i) for i in range(1, 9)]
    vals = [f(series[x]) for x in same if x in series and series[x]["operational_units"] > 0 and f(series[x]) is not None]
    if len(vals) >= 4:
        return vals, f"same weekday, last {len(vals)} weeks"
    trail = [d - timedelta(days=i) for i in range(1, 29)]
    vals = [f(series[x]) for x in trail if x in series and series[x]["operational_units"] > 0 and f(series[x]) is not None]
    return (vals, f"trailing {len(vals)} days") if len(vals) >= 7 else ([], "")


def detect(db: Session, venue_id: int, venue_name: str, d: date, cutoff_min: int | None = None) -> dict:
    series = analytics.daily_series(db, [venue_id], d - timedelta(days=60), d, cutoff_min)
    cur = series.get(d)
    findings, insufficient = [], []
    if not cur or cur["operational_units"] == 0:
        return {"venue_id": venue_id, "venue": venue_name, "date": d.isoformat(), "findings": [], "insufficient": []}
    for metric, (label, fmt, min_abs) in CHECKS.items():
        f = METRICS[metric]
        x = f(cur)
        if x is None:
            continue
        hist, basis = _baseline(series, d, f)
        if not hist:
            insufficient.append(metric)
            continue
        z = robust_z(x, hist)
        med = _quantile(hist, 0.5)
        if z is None or abs(z) < Z_THRESHOLD or abs(x - med) < min_abs:
            continue
        findings.append({
            "metric": metric, "label": label, "format": fmt, "current": x, "median": med,
            "range_low": _quantile(hist, 0.25), "range_high": _quantile(hist, 0.75),
            "pct_from_median": (x - med) / med if med else None,
            "z": None if abs(z) == float("inf") else round(z, 2),
            "direction": "above" if x > med else "below", "basis": basis, "samples": len(hist),
        })
    findings.sort(key=lambda x: -abs(x["z"] or 99))
    driver = _facility_driver(db, venue_id, d, cutoff_min) if any(
        f["metric"] in ("occupancy", "utilization") for f in findings) else None
    return {"venue_id": venue_id, "venue": venue_name, "date": d.isoformat(), "findings": findings,
            "insufficient": insufficient, "driver": driver}


def _facility_driver(db: Session, venue_id: int, d: date, cutoff_min: int | None) -> dict | None:
    """Which facility deviates most from its own same-weekday occupancy pattern."""
    start = d - timedelta(days=56)
    first = analytics.first_data_date(db)
    if first and start < first:
        start = first
    fac = analytics.compute(db, [venue_id], start, d, cutoff_min, with_facilities=True)["facilities"]
    from ..models import Facility
    best = None
    for fid in {k[0] for k in fac}:
        def occ(day):
            m = fac.get((fid, day))
            return m["occupied_units"] / m["sellable_units"] if m and m["sellable_units"] else None
        x = occ(d)
        hist = [v for v in (occ(d - timedelta(days=7 * i)) for i in range(1, 9)) if v is not None]
        if x is None or len(hist) < 4:
            continue
        z = robust_z(x, hist)
        if z is None or abs(z) == float("inf"):
            continue
        if best is None or abs(z) > abs(best["z"]):
            f = db.get(Facility, fid)
            best = {"facility_id": fid, "facility": f.name, "sport": f.sport, "current": x,
                    "median": _quantile(hist, 0.5), "z": round(z, 2)}
    return best if best and abs(best["z"]) >= 2 else None


def portfolio(db: Session, venues, d: date, cutoff_min: int | None = None) -> list[dict]:
    out = []
    for v in venues:
        r = detect(db, v.id, v.name, d, cutoff_min)
        if not r["findings"]:
            continue
        # One explainable insight per venue: the strongest deviation, plus what moved with it.
        f, others = r["findings"][0], r["findings"][1:]
        out.append({**f, "venue_id": v.id, "venue": v.name, "date": r["date"],
                    "correlated": [{"label": o["label"], "pct_from_median": o["pct_from_median"],
                                    "direction": o["direction"], "format": o["format"],
                                    "current": o["current"], "median": o["median"]} for o in others],
                    "driver": r.get("driver")})
    out.sort(key=lambda x: -abs(x["z"] or 99))
    return out
