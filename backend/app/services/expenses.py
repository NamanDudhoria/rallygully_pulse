"""Company expenses (V1 of the P&L): HQ, Community and Academy log them; Venue Managers' cash expenses are added in.

An expense has a date, amount, category (+ subcategory), where it was spent, notes and a venue; with no venue it is a
central expense. "Others" needs the category written in. Revenue comes from Pulse; the P&L itself is V2.
"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..audit import DomainError
from ..models import Expense, User, Venue, VmCashEntry
from ..permissions import can
from ..timeutil import now_local

OTHERS = "Others"
CATEGORIES: dict[str, list[str]] = {
    "Rent & Utilities": ["Rent", "Electricity", "Water", "Internet & phone", "Other utilities"],
    "Staff": ["Salaries", "Incentives", "Training", "Staff welfare"],
    "Maintenance & Repairs": ["Court maintenance", "Equipment repair", "Cleaning", "Facility upkeep"],
    "Equipment & Supplies": ["Sports equipment", "Consumables", "Store stock purchase", "Uniforms"],
    "Marketing": ["Ads", "Promotions & offers", "Content & design", "Partnerships"],
    "Community & Events": ["Prizes", "Refreshments", "Event costs", "Coaches & referees"],
    "Travel & Logistics": ["Local travel", "Transport of goods", "Courier"],
    "Software & Subscriptions": ["Software", "Payment & SMS charges", "Subscriptions"],
    "Professional & Legal": ["Accounting", "Legal", "Licences & fees", "Insurance"],
    OTHERS: [],
}
CENTRAL = "Central"
VM_CATEGORY = "Venue cash expense"


def _clean(v: str | None, n: int) -> str | None:
    return " ".join((v or "").split())[:n] or None


def _validate(db: Session, *, spent_on: date, amount: float, venue_id: int | None, location: str | None,
              category: str, subcategory: str | None, category_other: str | None, notes: str | None) -> dict:
    if category not in CATEGORIES:
        raise DomainError("Choose a category.", 422)
    if not 0 < amount <= 100_000_000:
        raise DomainError("Enter an amount above zero.", 422)
    if spent_on > now_local().date():
        raise DomainError("The date can't be in the future.", 422)
    if venue_id is not None and not db.get(Venue, venue_id):
        raise DomainError("Venue not found.", 404)
    sub = _clean(subcategory, 40)
    if category == OTHERS:
        sub = None
        other = _clean(category_other, 60)
        if not other:
            raise DomainError("Write the category for 'Others'.", 422)
    else:
        other = None
        if sub and sub not in CATEGORIES[category]:
            raise DomainError("Choose a subcategory from the list.", 422)
    return {"spent_on": spent_on, "amount": round(amount, 2), "venue_id": venue_id, "location": _clean(location, 120),
            "category": category, "subcategory": sub, "category_other": other, "notes": _clean(notes, 300)}


def _snap(e: Expense) -> dict:
    return {"spent_on": e.spent_on, "amount": e.amount, "venue_id": e.venue_id, "location": e.location,
            "category": e.category, "subcategory": e.subcategory, "category_other": e.category_other, "notes": e.notes}


def create(db: Session, actor: User, **f) -> Expense:
    e = Expense(**_validate(db, **f), created_by=actor.id)
    db.add(e)
    db.flush()
    audit.record(db, actor, "expense.create", "expense", e.id, None, _snap(e), e.venue_id)
    return e


def _may_change(actor: User, e: Expense) -> None:
    if not (can(actor, "corrections") or e.created_by == actor.id):
        raise DomainError("Only the person who logged this expense, or HQ, can change it.", 403)


def update(db: Session, actor: User, e: Expense, **f) -> Expense:
    _may_change(actor, e)
    before = _snap(e)
    for k, v in _validate(db, **f).items():
        setattr(e, k, v)
    audit.record(db, actor, "expense.update", "expense", e.id, before, _snap(e), e.venue_id)
    return e


def delete(db: Session, actor: User, e: Expense) -> None:
    _may_change(actor, e)
    audit.record(db, actor, "expense.delete", "expense", e.id, _snap(e), None, e.venue_id)
    db.delete(e)


def report(db: Session, d0: date, d1: date, venue_id: int | None = None, central_only: bool = False) -> dict:
    """Rows (HQ-logged and VM cash expenses together, newest first) with totals by venue and by category."""
    names = {i: n for i, n in db.execute(select(User.id, User.name))}
    venues = {v.id: v.name for v in db.scalars(select(Venue))}
    rows = []
    q = select(Expense).where(Expense.spent_on >= d0, Expense.spent_on <= d1)
    if central_only:
        q = q.where(Expense.venue_id.is_(None))
    elif venue_id is not None:
        q = q.where(Expense.venue_id == venue_id)
    for e in db.scalars(q):
        cat = e.category_other if e.category == OTHERS else e.category
        rows.append({"id": e.id, "source": "hq", "date": e.spent_on, "amount": e.amount, "venue_id": e.venue_id,
                     "venue": venues.get(e.venue_id) or CENTRAL, "location": e.location, "category": e.category,
                     "subcategory": e.subcategory, "category_other": e.category_other, "label": cat,
                     "notes": e.notes, "by": names.get(e.created_by), "by_id": e.created_by, "sort": e.created_at})
    if not central_only:
        vq = select(VmCashEntry).where(VmCashEntry.kind == "expense", VmCashEntry.created_at >= _start(d0),
                                       VmCashEntry.created_at < _start(d1, 1))
        if venue_id is not None:
            vq = vq.where(VmCashEntry.venue_id == venue_id)
        for x in db.scalars(vq):
            rows.append({"id": x.id, "source": "vm", "date": x.created_at.date(), "amount": x.amount,
                         "venue_id": x.venue_id, "venue": venues.get(x.venue_id), "location": None,
                         "category": VM_CATEGORY, "subcategory": None, "category_other": None, "label": VM_CATEGORY,
                         "notes": x.message, "by": names.get(x.user_id), "by_id": x.user_id, "sort": x.created_at})
    rows.sort(key=lambda r: (r["date"], r["sort"]), reverse=True)
    by_venue: dict[int | None, dict] = {}
    by_cat: dict[str, float] = {}
    for r in rows:
        b = by_venue.setdefault(r["venue_id"], {"venue_id": r["venue_id"], "name": r["venue"], "hq": 0.0, "vm": 0.0,
                                                "total": 0.0})
        b[r["source"]] = round(b[r["source"]] + r["amount"], 2)
        b["total"] = round(b["total"] + r["amount"], 2)
        by_cat[r["label"]] = round(by_cat.get(r["label"], 0.0) + r["amount"], 2)
        r.pop("sort")
    hq = round(sum(r["amount"] for r in rows if r["source"] == "hq"), 2)
    vm = round(sum(r["amount"] for r in rows if r["source"] == "vm"), 2)
    return {"date_from": d0, "date_to": d1, "rows": rows,
            "totals": {"total": round(hq + vm, 2), "hq": hq, "vm": vm},
            "by_venue": sorted(by_venue.values(), key=lambda b: -b["total"]),
            "by_category": [{"category": c, "total": t} for c, t in sorted(by_cat.items(), key=lambda x: -x[1])]}


def _start(d: date, plus: int = 0):
    from datetime import datetime, timedelta
    return datetime.combine(d + timedelta(days=plus), datetime.min.time())
