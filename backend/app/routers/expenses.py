"""Company expenses: HQ, Community and Academy log them; the report adds in Venue Managers' cash expenses."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Expense, User
from ..permissions import require
from ..services import expenses
from ..timeutil import today
from .common import get_or_404

router = APIRouter(prefix="/api/expenses", tags=["expenses"])


class ExpenseIn(BaseModel):
    spent_on: date
    amount: float
    venue_id: int | None = None  # none = central expense
    location: str | None = None
    category: str
    subcategory: str | None = None
    category_other: str | None = None
    notes: str | None = None


@router.get("/categories")
def categories(user: User = Depends(require("expenses.manage"))):
    return {"categories": expenses.CATEGORIES, "others": expenses.OTHERS}


@router.get("")
def report(date_from: date | None = None, date_to: date | None = None, venue_id: int | None = None,
           central: bool = False, user: User = Depends(require("expenses.manage")), db: Session = Depends(get_db)):
    d1 = date_to or today()
    d0 = date_from or d1.replace(day=1)
    if d1 < d0 or (d1 - d0).days > 400:
        raise HTTPException(422, "Choose up to 400 days.")
    return expenses.report(db, d0, d1, venue_id, central)


@router.post("")
def create(body: ExpenseIn, user: User = Depends(require("expenses.manage")), db: Session = Depends(get_db)):
    e = expenses.create(db, user, **body.model_dump())
    db.commit()
    return {"id": e.id}


@router.put("/{expense_id}")
def update(expense_id: int, body: ExpenseIn, user: User = Depends(require("expenses.manage")),
           db: Session = Depends(get_db)):
    e = expenses.update(db, user, get_or_404(db, Expense, expense_id), **body.model_dump())
    db.commit()
    return {"id": e.id}


@router.delete("/{expense_id}")
def delete(expense_id: int, user: User = Depends(require("expenses.manage")), db: Session = Depends(get_db)):
    expenses.delete(db, user, get_or_404(db, Expense, expense_id))
    db.commit()
    return {"ok": True}
