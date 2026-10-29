"""RG Store: food and drink sold at each venue, with stock kept per product.

Each venue has its own product list, prices and stock. HQ (Admin or HQ) sets products and prices, restocks and
corrects stock, and voids sales; a Venue Manager sells at the venue they are rostered and clocked in at. Every
sale takes its items out of stock in the same transaction, and a sale that would take a product below zero is
refused as a whole. No message is sent to the customer. Store money is kept apart from court revenue; cash sales
count in the venue's cash balance.
"""
from collections import Counter

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import audit, config
from ..audit import DomainError
from ..models import Customer, StoreProduct, StoreSale, StoreSaleItem, StoreStockMove, User, Venue
from ..timeutil import now_local
from . import customers, notify
from .bookings import PAYMENT_METHODS

MAX_QTY = 50


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name or len(name) > 80:
        raise DomainError("Give the product a name of up to 80 characters.", 422)
    return name


def _check_price(price: float) -> float:
    if not 0 < price <= 100_000:
        raise DomainError("Enter a price above 0.", 422)
    return round(price, 2)


def _check_level(level: int) -> int:
    if not 0 <= level <= 10_000:
        raise DomainError("The low-stock level must be between 0 and 10,000.", 422)
    return level


def _move(db: Session, p: StoreProduct, kind: str, qty: int, actor: User | None, *, note: str | None = None,
          unit_cost: float | None = None, sale_id: int | None = None) -> StoreStockMove:
    m = StoreStockMove(product_id=p.id, venue_id=p.venue_id, kind=kind, quantity=qty, stock_after=p.stock,
                       unit_cost=unit_cost, note=note, sale_id=sale_id, actor_id=actor.id if actor else None)
    db.add(m)
    return m


# ── Products and stock (HQ) ──────────────────────────────────────────────────
def create_product(db: Session, actor: User, venue_id: int, *, name: str, category: str | None, price: float,
                   low_stock_level: int, opening_stock: int = 0) -> StoreProduct:
    if not db.get(Venue, venue_id):
        raise DomainError("Venue not found.", 404)
    name = _clean_name(name)
    if db.scalar(select(StoreProduct.id).where(StoreProduct.venue_id == venue_id, StoreProduct.name == name)):
        raise DomainError(f"{name} is already in this venue's store.", 409)
    if not 0 <= opening_stock <= 100_000:
        raise DomainError("Opening stock must be between 0 and 100,000.", 422)
    p = StoreProduct(venue_id=venue_id, name=name, category=(category or "").strip()[:40] or "Other",
                     price=_check_price(price), low_stock_level=_check_level(low_stock_level), stock=0)
    db.add(p)
    db.flush()
    audit.record(db, actor, "store.product_create", "store_product", p.id, None,
                 {"name": p.name, "price": p.price, "category": p.category}, venue_id)
    if opening_stock:
        restock(db, actor, p, opening_stock, None, "Opening stock")
    return p


def update_product(db: Session, actor: User, p: StoreProduct, *, name: str, category: str | None, price: float,
                   low_stock_level: int, active: bool) -> StoreProduct:
    name = _clean_name(name)
    if name != p.name and db.scalar(select(StoreProduct.id).where(StoreProduct.venue_id == p.venue_id,
                                                                  StoreProduct.name == name)):
        raise DomainError(f"{name} is already in this venue's store.", 409)
    before = product_dict(p)
    p.name, p.category = name, (category or "").strip()[:40] or "Other"
    p.price, p.low_stock_level, p.active = _check_price(price), _check_level(low_stock_level), active
    audit.record(db, actor, "store.product_update", "store_product", p.id, before, product_dict(p), p.venue_id)
    return p


def restock(db: Session, actor: User, p: StoreProduct, quantity: int, unit_cost: float | None, note: str | None) -> StoreProduct:
    if not 1 <= quantity <= 100_000:
        raise DomainError("Restock between 1 and 100,000 units.", 422)
    if unit_cost is not None and not 0 <= unit_cost <= 100_000:
        raise DomainError("The cost per unit can't be negative.", 422)
    p.stock += quantity
    _move(db, p, "restock", quantity, actor, note=(note or "").strip()[:200] or None, unit_cost=unit_cost)
    audit.record(db, actor, "store.restock", "store_product", p.id, None,
                 {"quantity": quantity, "stock": p.stock, "unit_cost": unit_cost}, p.venue_id)
    return p


def adjust(db: Session, actor: User, p: StoreProduct, delta: int, reason: str) -> StoreProduct:
    """Correct the count, e.g. damaged, expired or a stock-take difference. Stock can't go below zero."""
    reason = (reason or "").strip()
    if not delta:
        raise DomainError("Enter how many to add or take off.", 422)
    if not reason:
        raise DomainError("Give a reason for the correction.", 422)
    if p.stock + delta < 0:
        raise DomainError(f"Only {p.stock} in stock: you can take off at most {p.stock}.", 422)
    p.stock += delta
    _move(db, p, "adjust", delta, actor, note=reason[:200])
    audit.record(db, actor, "store.adjust", "store_product", p.id, None, {"delta": delta, "stock": p.stock,
                                                                          "reason": reason}, p.venue_id)
    return p


# ── Selling ──────────────────────────────────────────────────────────────────
def sell(db: Session, actor: User, venue_id: int, items: list[tuple[int, int]], *, phone: str, name: str | None,
         email: str | None, method: str) -> StoreSale:
    """Record a confirmed purchase and take its items out of stock, all or nothing."""
    if method not in PAYMENT_METHODS:
        raise DomainError("Choose a payment method: cash, UPI, Razorpay or other.", 422)
    if not items:
        raise DomainError("Add at least one item.", 422)
    wanted = Counter()
    for pid, qty in items:
        if not 1 <= qty <= MAX_QTY:
            raise DomainError(f"Quantity must be between 1 and {MAX_QTY}.", 422)
        wanted[pid] += qty
    products = {p.id: p for p in db.scalars(select(StoreProduct).where(StoreProduct.id.in_(list(wanted))))}
    for pid in wanted:
        p = products.get(pid)
        if not p or p.venue_id != venue_id or not p.active:
            raise DomainError("One of those items isn't sold at this venue.", 422)
    customer, _ = customers.get_or_create(db, phone, name, email)
    # Take stock first, in one statement per product that only succeeds if enough is left, so two people selling
    # the last bottle at once can't both succeed. Any shortfall raises before the session commits: nothing is kept.
    before = {pid: products[pid].stock for pid in wanted}
    for pid, qty in wanted.items():
        res = db.execute(update(StoreProduct).where(StoreProduct.id == pid, StoreProduct.stock >= qty)
                         .values(stock=StoreProduct.stock - qty).execution_options(synchronize_session=False))
        if res.rowcount != 1:
            db.refresh(products[pid])
            left = products[pid].stock
            raise DomainError(f"Only {left} {products[pid].name} left in stock." if left else
                              f"{products[pid].name} is out of stock.", 409)
    for p in products.values():
        db.refresh(p)
    sale = StoreSale(venue_id=venue_id, customer_id=customer.id, payment_method=method, status="confirmed",
                     paid_at=now_local(), sold_by=actor.id, total=0.0)
    for pid, qty in wanted.items():
        p = products[pid]
        sale.items.append(StoreSaleItem(product_id=p.id, name=p.name, unit_price=p.price, quantity=qty,
                                        amount=round(p.price * qty, 2)))
    sale.total = round(sum(i.amount for i in sale.items), 2)
    db.add(sale)
    db.flush()
    for pid, qty in wanted.items():
        _move(db, products[pid], "sale", -qty, actor, sale_id=sale.id)
    audit.record(db, actor, "store.sale", "store_sale", sale.id, None,
                 {"total": sale.total, "method": method, "items": [(i.name, i.quantity) for i in sale.items]}, venue_id)
    _alert_low_stock(db, [(products[pid], before[pid]) for pid in wanted])
    return sale


def void_sale(db: Session, actor: User, sale: StoreSale, reason: str) -> StoreSale:
    """HQ cancels a sale: its items go back into stock and it no longer counts anywhere."""
    if sale.status == "voided":
        raise DomainError("This sale is already voided.")
    reason = (reason or "").strip()
    if not reason:
        raise DomainError("Give a reason for voiding the sale.", 422)
    for it in sale.items:
        p = db.get(StoreProduct, it.product_id)
        p.stock += it.quantity
        _move(db, p, "void", it.quantity, actor, sale_id=sale.id, note=reason[:200])
    sale.status, sale.voided_at, sale.voided_by, sale.void_reason = "voided", now_local(), actor.id, reason[:200]
    audit.record(db, actor, "store.void", "store_sale", sale.id, None, {"reason": reason, "total": sale.total}, sale.venue_id)
    return sale


# ── Messages ─────────────────────────────────────────────────────────────────
def receipt_no(sale: StoreSale) -> str:
    return f"RG-{sale.id:05d}"


def _admins(db: Session) -> list[str]:
    return [e for e in db.scalars(select(User.email).where(User.role.in_(("hq_admin", "hq")), User.active.is_(True))
                                  .order_by(User.id))] or [config.OPS_EMAIL]


def _alert_low_stock(db: Session, changed: list[tuple[StoreProduct, int]]) -> int:
    """Email HQ when a sale takes a product down to its low-stock level (once, as it crosses)."""
    low = [p for p, before in changed if before > p.low_stock_level >= p.stock]
    if not low:
        return 0
    v = db.get(Venue, low[0].venue_id)
    venue = v.name.removeprefix("RallyGully ").strip()
    body = (f"These RG Store products at {venue} are running low:\n\n"
            + "\n".join(f"  • {p.name}: {p.stock} left (low-stock level {p.low_stock_level})" for p in low)
            + "\n\nRestock them in Pulse under RG Store → Products.")
    subject = f"[Pulse] Low stock at {venue}: " + ", ".join(p.name for p in low)
    for to in _admins(db):
        notify.email(db, to, subject, body, "venue", v.id, template="store_low_stock")
    return len(low)


# ── Read models ──────────────────────────────────────────────────────────────
def product_dict(p: StoreProduct) -> dict:
    return {"id": p.id, "venue_id": p.venue_id, "name": p.name, "category": p.category, "price": p.price,
            "stock": p.stock, "low_stock_level": p.low_stock_level, "active": p.active,
            "low": p.stock <= p.low_stock_level, "out": p.stock <= 0}


def sale_dict(db: Session, s: StoreSale, names: dict | None = None) -> dict:
    c = db.get(Customer, s.customer_id)
    who = names.get(s.sold_by) if names is not None else (db.get(User, s.sold_by).name if s.sold_by else None)
    return {"id": s.id, "receipt": receipt_no(s), "venue_id": s.venue_id, "total": s.total,
            "payment_method": s.payment_method, "status": s.status, "paid_at": s.paid_at,
            "customer": {"name": c.name, "phone": c.phone}, "sold_by": who,
            "void_reason": s.void_reason, "voided_at": s.voided_at,
            "items": [{"product_id": i.product_id, "name": i.name, "unit_price": i.unit_price,
                       "quantity": i.quantity, "amount": i.amount} for i in s.items]}
