import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import func, select

from accounting.balance_sheet import balance_sheet
from accounting.profit_loss import profit_and_loss
from database.models import Account, JournalLine, Transaction
from inventory.stock_manager import stock_quantity, stock_value
from pages.common import current_business_id, product_stock_frame, session as open_session
from reports.daily_report import daily_totals
from utils.currency import format_naira


def _account_balance(session, code):
    account = session.scalar(
        select(Account).where(
            Account.business_id == current_business_id(), Account.code == code
        )
    )
    if account is None:
        return 0
    debit, credit = session.execute(
        select(
            func.coalesce(func.sum(JournalLine.debit_kobo), 0),
            func.coalesce(func.sum(JournalLine.credit_kobo), 0),
        ).where(JournalLine.account_id == account.id)
    ).one()
    return int(debit - credit) if account.account_type == "asset" else int(credit - debit)


def render():
    db = open_session()
    business_id = current_business_id()
    try:
        _render_dashboard(db, business_id)
    finally:
        db.close()


def _render_dashboard(session, business_id):
    st.title("Dashboard")
    st.caption("A clear picture of today's business and your overall financial health.")
    today = daily_totals(session, business_id)
    profit = profit_and_loss(session, business_id)
    statement = balance_sheet(session, business_id)
    inventory = stock_value(session, business_id)
    receivables = _account_balance(session, "1100")
    payables = _account_balance(session, "2000")
    cash, bank = _account_balance(session, "1010"), _account_balance(session, "1020")

    cards = [
        ("Sales today", format_naira(today["sale"], kobo=True)),
        ("Purchases today", format_naira(today["purchase"], kobo=True)),
        ("Actual profit", format_naira(profit["net_profit"], kobo=True)),
        ("Cash in shop", format_naira(cash, kobo=True)),
        ("Bank / POS", format_naira(bank, kobo=True)),
        ("Goods remaining", format_naira(inventory, kobo=True)),
        ("Customers owe you", format_naira(receivables, kobo=True)),
        ("You owe suppliers", format_naira(payables, kobo=True)),
    ]
    for index in range(0, len(cards), 4):
        columns = st.columns(4)
        for column, (label, value) in zip(columns, cards[index:index + 4]):
            column.metric(label, value)

    stock = product_stock_frame(session)
    low_stock = stock[stock["Status"] == "Restock"] if not stock.empty else stock
    if not low_stock.empty:
        st.warning(f"{len(low_stock)} product(s) are at or below their restock level.")
        st.dataframe(low_stock, width="stretch", hide_index=True)

    st.subheader("Trading at a glance")
    transactions = list(session.scalars(
        select(Transaction).where(
            Transaction.business_id == business_id,
            Transaction.transaction_type.in_(("sale", "sales_return", "expense")),
            Transaction.status == "posted",
        ).order_by(Transaction.transaction_date)
    ))
    if transactions:
        frame = pd.DataFrame([
            {
                "Month": row.transaction_date.strftime("%Y-%m"),
                "Type": "sale" if row.transaction_type in ("sale", "sales_return") else "expense",
                "Kobo": -row.total_kobo if row.transaction_type == "sales_return" else row.total_kobo,
            }
            for row in transactions
        ])
        frame = frame.groupby(["Month", "Type"], as_index=False)["Kobo"].sum()
        frame["Amount (₦)"] = frame["Kobo"] / 100
        chart = px.bar(
            frame, x="Month", y="Amount (₦)", color="Type", barmode="group",
            color_discrete_map={"sale": "#35b89b", "expense": "#ef8d76"},
        )
        st.plotly_chart(chart, width="stretch")
    else:
        st.info("Your sales and expenses will appear here after you record them.")

    if not statement["balances"]:
        st.error("Accounting check: assets do not equal liabilities plus equity.")
    else:
        st.caption("Accounting check passed — assets equal liabilities plus equity.")
