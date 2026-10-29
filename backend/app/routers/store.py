"""RG Store: products and stock (HQ), selling (VM at their venue, or HQ), sales and voids."""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..auth import current_user
from ..db import get_db
from ..models import StoreProduct, StoreSale, StoreStockMove, User, Venue
from ..permissions import can, ensure_venue, require
from ..services import store
from ..timeutil import today
from .common import get_or_404

router = APIRouter(prefix="/api/store", tags=["store"])


def _hq(user: User) -> bool:
    return can(user, "store.manage")


def _venue_access(db: Session, user: User, venue_id: int, write: bool = False) -> Venue:
    v = get_or_404(db, Venue, venue_id)
    if not _hq(user):
        ensure_venue(db, user, venue_id, today(), write=write)  # a VM: rostered and clocked in there today
    return v


class ProductIn(BaseModel):
    name: str
    category: str | None = None
    price: float
    low_stock_level: int = 5
    opening_stock: int = 0
    active: bool = True


class RestockIn(BaseModel):
    quantity: int
    unit_cost: float | None = None
    note: str | None = None


class AdjustIn(BaseModel):
    delta: int
    reason: str


class LineIn(BaseModel):
    product_id: int
    quantity: int = 1


class SaleIn(BaseModel):
    items: list[LineIn]
    phone: str
    name: str | None = None
    email: str | None = None
    method: str


class VoidIn(BaseModel):
    reason: str


@router.get("/{venue_id}/products")
def products(venue_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _venue_access(db, user, venue_id)
    q = select(StoreProduct).where(StoreProduct.venue_id == venue_id).order_by(StoreProduct.category, StoreProduct.name)
    if not _hq(user):
        q = q.where(StoreProduct.active.is_(True))
    return [store.product_dict(p) for p in db.scalars(q)]


@router.post("/{venue_id}/products")
def create_product(venue_id: int, body: ProductIn, user: User = Depends(require("store.manage")),
                   db: Session = Depends(get_db)):
    p = store.create_product(db, user, venue_id, name=body.name, category=body.category, price=body.price,
                             low_stock_level=body.low_stock_level, opening_stock=body.opening_stock)
    db.commit()
    return store.product_dict(p)


@router.put("/products/{product_id}")
def update_product(product_id: int, body: ProductIn, user: User = Depends(require("store.manage")),
                   db: Session = Depends(get_db)):
    p = store.update_product(db, user, get_or_404(db, StoreProduct, product_id), name=body.name,
                             category=body.category, price=body.price, low_stock_level=body.low_stock_level,
                             active=body.active)
    db.commit()
    return store.product_dict(p)


@router.post("/products/{product_id}/restock")
def restock(product_id: int, body: RestockIn, user: User = Depends(require("store.manage")),
            db: Session = Depends(get_db)):
    p = store.restock(db, user, get_or_404(db, StoreProduct, product_id), body.quantity, body.unit_cost, body.note)
    db.commit()
    return store.product_dict(p)


@router.post("/products/{product_id}/adjust")
def adjust(product_id: int, body: AdjustIn, user: User = Depends(require("store.manage")),
           db: Session = Depends(get_db)):
    p = store.adjust(db, user, get_or_404(db, StoreProduct, product_id), body.delta, body.reason)
    db.commit()
    return store.product_dict(p)


@router.get("/products/{product_id}/moves")
def moves(product_id: int, user: User = Depends(require("store.manage")), db: Session = Depends(get_db)):
    get_or_404(db, StoreProduct, product_id)
    names = {i: n for i, n in db.execute(select(User.id, User.name))}
    return [{"id": m.id, "kind": m.kind, "quantity": m.quantity, "stock_after": m.stock_after,
             "unit_cost": m.unit_cost, "note": m.note, "sale_id": m.sale_id, "by": names.get(m.actor_id),
             "at": m.created_at}
            for m in db.scalars(select(StoreStockMove).where(StoreStockMove.product_id == product_id)
                                .order_by(StoreStockMove.id.desc()).limit(200))]


@router.post("/{venue_id}/sales")
def sell(venue_id: int, body: SaleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not (_hq(user) or can(user, "venue.operate")):
        raise HTTPException(403, "You can't sell at the store.")
    _venue_access(db, user, venue_id, write=True)
    s = store.sell(db, user, venue_id, [(x.product_id, x.quantity) for x in body.items], phone=body.phone,
                   name=body.name, email=body.email, method=body.method)
    db.commit()
    return store.sale_dict(db, s)


@router.get("/{venue_id}/sales")
def sales(venue_id: int, date_from: date | None = None, date_to: date | None = None,
          user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A VM sees their venue's sales for today; HQ any range up to 92 days."""
    _venue_access(db, user, venue_id)
    d0, d1 = date_from or today(), date_to or date_from or today()
    if not _hq(user):
        d0 = d1 = today()
    if d1 < d0 or (d1 - d0).days > 92:
        raise HTTPException(422, "Choose up to 92 days.")
    rows = list(db.scalars(select(StoreSale).options(selectinload(StoreSale.items)).where(
        StoreSale.venue_id == venue_id, StoreSale.paid_at >= datetime.combine(d0, datetime.min.time()),
        StoreSale.paid_at < datetime.combine(d1 + timedelta(days=1), datetime.min.time()))
        .order_by(StoreSale.id.desc())))
    names = {i: n for i, n in db.execute(select(User.id, User.name))}
    live = [s for s in rows if s.status == "confirmed"]
    by_method: dict[str, float] = {}
    for s in live:
        by_method[s.payment_method] = round(by_method.get(s.payment_method, 0.0) + s.total, 2)
    top: dict[str, list] = {}
    for s in live:
        for i in s.items:
            t = top.setdefault(i.name, [0, 0.0])
            t[0] += i.quantity
            t[1] += i.amount
    return {"date_from": d0, "date_to": d1,
            "totals": {"sales": len(live), "revenue": round(sum(s.total for s in live), 2), "by_method": by_method,
                       "voided": len(rows) - len(live)},
            "top_items": [{"name": n, "quantity": q, "amount": round(a, 2)}
                          for n, (q, a) in sorted(top.items(), key=lambda x: -x[1][1])[:10]],
            "sales": [store.sale_dict(db, s, names) for s in rows]}


@router.post("/sales/{sale_id}/void")
def void(sale_id: int, body: VoidIn, user: User = Depends(require("store.manage")), db: Session = Depends(get_db)):
    s = store.void_sale(db, user, get_or_404(db, StoreSale, sale_id), body.reason)
    db.commit()
    return store.sale_dict(db, s)


# Razorpay webhook (not wired in yet; see app/services/razorpay.py)
# @router.post("/razorpay/webhook")
# async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
#     raw = await request.body()
#     if not razorpay.verify_webhook(raw, request.headers.get("X-Razorpay-Signature", "")):
#         raise HTTPException(400, "Bad signature")
#     event = json.loads(raw)
#     if event.get("event") == "payment_link.paid":
#         ref = event["payload"]["payment_link"]["entity"]["reference_id"]   # "store-<sale id>"
#         sale = db.get(StoreSale, int(ref.split("-", 1)[1]))
#         if sale and sale.status == "pending":
#             sale.status, sale.paid_at = "confirmed", now_local()
#             db.commit()
#     return {"ok": True}
