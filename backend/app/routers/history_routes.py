"""Historical sales (imported from the pre-Pulse monthly workbooks): portfolio, venue-month detail, data notes."""
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..history.parse import VERTICAL
from ..models import HistIssue, HistPnl, HistRevenue, HistSource, User, Venue
from ..permissions import require

router = APIRouter(prefix="/api/history", tags=["history"])

CHANNEL_LABEL = {
    "district": "District", "hudle": "Hudle", "playo": "Playo", "khelomore": "Khelomore", "zplys": "Zplys",
    "offline_bookings": "Offline bookings", "memberships": "Memberships", "pickup_games": "Pickup games",
    "social_events": "Social events & tournaments", "community_partnerships": "Community partnerships",
    "courtly": "Courtly", "sportomic": "Sportomic", "misfits": "Misfits", "academy": "Academy", "coaching": "Coaching",
    "corporate_events": "Corporate events", "b2b_partnerships": "B2B partnerships", "brand_activation": "Brand activation",
    "product_rental": "Product rental", "product_sales": "Product sales", "fnb": "F&B", "other_income": "Other income",
}
VERTICAL_LABEL = {"online": "Online platforms", "offline": "Offline & memberships", "community": "Community",
                  "academy": "Academy & coaching", "b2b": "B2B & events", "commerce": "Commerce", "other": "Other income"}


def _key(y, m):
    return f"{y:04d}-{m:02d}"


@router.get("/overview")
def overview(venue_id: int | None = None, user: User = Depends(require("portfolio.view")), db: Session = Depends(get_db)):
    q = select(HistRevenue.venue_id, HistRevenue.year, HistRevenue.month, HistRevenue.vertical, HistRevenue.channel,
               HistRevenue.sport, HistRevenue.gross, HistRevenue.net, HistRevenue.hours)
    if venue_id:
        q = q.where(HistRevenue.venue_id == venue_id)
    rows = db.execute(q).all()
    if not rows:
        return {"months": [], "venues": [], "cells": {}, "by_vertical": {}, "by_channel": {}, "by_sport": {},
                "totals": {}, "sources": _source_counts(db), "issues": _issue_counts(db)}
    months = sorted({_key(y, m) for _, y, m, *_ in rows})
    cells = defaultdict(float)
    by_vertical = defaultdict(lambda: defaultdict(float))
    by_channel = defaultdict(float)
    by_sport = defaultdict(float)
    per_month = defaultdict(float)
    for vid, y, m, vert, ch, sp, g, _net, _h in rows:
        k = _key(y, m)
        cells[f"{vid}|{k}"] += g
        by_vertical[vert][k] += g
        by_channel[ch] += g
        by_sport[sp or "Unspecified"] += g
        per_month[k] += g
    venues = {v.id: v for v in db.scalars(select(Venue))}
    vids = sorted({r[0] for r in rows}, key=lambda i: -sum(cells.get(f"{i}|{k}", 0) for k in months))
    pnl = defaultdict(lambda: defaultdict(float))
    pq = select(HistPnl.year, HistPnl.month, HistPnl.category, HistPnl.amount)
    if venue_id:
        pq = pq.where(HistPnl.venue_id == venue_id)
    for y, m, cat, amt in db.execute(pq):
        if amt is not None:
            pnl[cat][_key(y, m)] += amt
    return {
        "months": months,
        "venues": [{"id": i, "name": venues[i].name, "code": venues[i].code,
                    "total": round(sum(cells.get(f"{i}|{k}", 0) for k in months), 2)} for i in vids],
        "cells": {k: round(v, 2) for k, v in cells.items()},
        "per_month": {k: round(v, 2) for k, v in per_month.items()},
        "by_vertical": {v: {k: round(x, 2) for k, x in d.items()} for v, d in by_vertical.items()},
        "vertical_labels": VERTICAL_LABEL,
        "by_channel": sorted(({"channel": c, "label": CHANNEL_LABEL.get(c, c), "vertical": VERTICAL.get(c), "gross": round(x, 2)}
                              for c, x in by_channel.items()), key=lambda r: -r["gross"]),
        "by_sport": sorted(({"sport": s, "gross": round(x, 2)} for s, x in by_sport.items()), key=lambda r: -r["gross"]),
        "pnl": {c: {k: round(x, 2) for k, x in d.items()} for c, d in pnl.items()},
        "total": round(sum(per_month.values()), 2),
        "sources": _source_counts(db),
        "issues": _issue_counts(db),
    }


@router.get("/venue/{venue_id}/{month}")
def venue_month(venue_id: int, month: str, user: User = Depends(require("portfolio.view")), db: Session = Depends(get_db)):
    try:
        y, m = (int(x) for x in month.split("-"))
    except ValueError:
        raise HTTPException(422, "month must be YYYY-MM") from None
    v = db.get(Venue, venue_id)
    if not v:
        raise HTTPException(404, "Venue not found")
    lines = list(db.scalars(select(HistRevenue).where(HistRevenue.venue_id == venue_id, HistRevenue.year == y,
                                                      HistRevenue.month == m).order_by(HistRevenue.date_from, HistRevenue.id)))
    channels = defaultdict(lambda: {"gross": 0.0, "net": 0.0, "net_known": True, "lines": 0})
    sports = defaultdict(float)
    daily = defaultdict(lambda: defaultdict(float))
    hours = defaultdict(float)
    for l in lines:
        c = channels[l.channel]
        c["gross"] += l.gross
        c["lines"] += 1
        if l.net is None:
            c["net_known"] = False
        else:
            c["net"] += l.net
        sports[l.sport or "Unspecified"] += l.gross
        if l.basis == "day":
            daily[l.date_from.isoformat()][l.vertical] += l.gross
            if l.hours:
                hours[l.date_from.isoformat()] += l.hours
    srcs = list(db.scalars(select(HistSource).where(HistSource.venue_id == venue_id, HistSource.year == y, HistSource.month == m)))
    sids = [s.id for s in srcs]
    return {
        "venue": {"id": v.id, "name": v.name, "code": v.code}, "month": month,
        "gross": round(sum(l.gross for l in lines), 2),
        "channels": sorted(({"channel": k, "label": CHANNEL_LABEL.get(k, k), "vertical": VERTICAL.get(k),
                             "gross": round(c["gross"], 2), "net": round(c["net"], 2) if c["net_known"] else None,
                             "lines": c["lines"]} for k, c in channels.items()), key=lambda r: -r["gross"]),
        "sports": sorted(({"sport": s, "gross": round(x, 2)} for s, x in sports.items()), key=lambda r: -r["gross"]),
        "daily": [{"date": d, **{k: round(x, 2) for k, x in vals.items()}, "hours": round(hours.get(d, 0), 2)}
                  for d, vals in sorted(daily.items())],
        "lines": [{"id": l.id, "date_from": l.date_from, "date_to": l.date_to, "basis": l.basis, "channel": l.channel,
                   "label": CHANNEL_LABEL.get(l.channel, l.channel), "sport": l.sport, "gross": l.gross, "net": l.net,
                   "commission": l.commission, "gateway": l.gateway, "tds": l.tds, "tcs": l.tcs, "hours": l.hours,
                   "counterparty": l.counterparty, "description": l.description, "sheet": l.sheet, "row": l.row}
                  for l in lines if l.basis != "day"],
        "day_line_count": sum(1 for l in lines if l.basis == "day"),
        "pnl": [{"category": p.category, "label": p.label, "amount": p.amount, "note": p.note}
                for p in db.scalars(select(HistPnl).where(HistPnl.source_id.in_(sids)).order_by(HistPnl.id))] if sids else [],
        "sources": [{"file": s.file, "sheet": s.sheet, "status": s.status, "reason": s.reason,
                     "stated_total": s.stated_total, "ingested_total": s.ingested_total} for s in srcs],
        "issues": [{"severity": i.severity, "code": i.code, "message": i.message}
                   for i in db.scalars(select(HistIssue).where(HistIssue.source_id.in_(sids)))] if sids else [],
    }


@router.get("/issues")
def issues(user: User = Depends(require("portfolio.view")), db: Session = Depends(get_db)):
    venues = {v.id: v for v in db.scalars(select(Venue))}
    srcs = {s.id: s for s in db.scalars(select(HistSource))}
    out = []
    for i in db.scalars(select(HistIssue).order_by(HistIssue.id)):
        s = srcs.get(i.source_id)
        out.append({"severity": i.severity, "code": i.code, "message": i.message,
                    "venue": venues[i.venue_id].name if i.venue_id in venues else None,
                    "month": _key(i.year, i.month) if i.year and i.month else None, "file": s.file if s else None})
    order = {"error": 0, "warn": 1, "info": 2}
    out.sort(key=lambda x: (order.get(x["severity"], 3), x["month"] or "", x["venue"] or ""))
    return {"issues": out,
            "sources": [{"file": s.file, "sheet": s.sheet, "status": s.status, "reason": s.reason,
                         "venue": venues[s.venue_id].name if s.venue_id in venues else s.venue_label,
                         "month": _key(s.year, s.month) if s.year and s.month else None,
                         "stated_total": s.stated_total, "ingested_total": s.ingested_total}
                        for s in sorted(srcs.values(), key=lambda s: (s.status != "ingested", s.year or 0, s.month or 0, s.file))]}


def _source_counts(db):
    c = defaultdict(int)
    for (st,) in db.execute(select(HistSource.status)):
        c[st] += 1
    return dict(c)


def _issue_counts(db):
    c = defaultdict(int)
    for (sv,) in db.execute(select(HistIssue.severity)):
        c[sv] += 1
    return dict(c)
