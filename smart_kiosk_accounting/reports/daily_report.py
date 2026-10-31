from datetime import datetime, time, timedelta

from sqlalchemy import func, select

from database.models import Transaction
from utils.helpers import lagos_now


def daily_totals(session, business_id: int, day=None):
    day = day or lagos_now().date()
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    totals = {}
    for kind in ("sale", "sales_return", "purchase", "purchase_return", "expense"):
        totals[kind] = int(
            session.scalar(
                select(func.coalesce(func.sum(Transaction.total_kobo), 0)).where(
                    Transaction.business_id == business_id,
                    Transaction.transaction_type == kind,
                    Transaction.status == "posted",
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
