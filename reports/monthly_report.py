from datetime import datetime

from sqlalchemy import func, select

from database.models import Transaction


def monthly_totals(session, business_id: int, year: int, month: int):
    start = datetime(year, month, 1)
    end = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1)
    totals = {}
    for kind in ("sale", "sales_return", "purchase", "purchase_return", "expense"):
        totals[kind] = int(
            session.scalar(
                select(func.coalesce(func.sum(Transaction.total_kobo), 0)).where(
                    Transaction.business_id == business_id,
                    Transaction.transaction_type == kind,
                    Transaction.transaction_date >= start,
                    Transaction.transaction_date < end,
                )
            ) or 0
        )
    return {
        "sale": totals["sale"] - totals["sales_return"],
        "sales_returns": totals["sales_return"],
        "purchase": totals["purchase"] - totals["purchase_return"],
        "purchase_returns": totals["purchase_return"],
        "expense": totals["expense"],
    }
