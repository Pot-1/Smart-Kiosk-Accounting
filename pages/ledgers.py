import pandas as pd
import streamlit as st
from sqlalchemy import select

from accounting.ledger_engine import ledger_rows
from database.models import Account
from pages.common import current_business_id, session
from utils.currency import format_naira


def render():
    st.title("General ledger")
    st.caption("A running history of the money posted to each account.")
    db = session()
    try:
        accounts = list(db.scalars(
            select(Account).where(Account.business_id == current_business_id())
            .order_by(Account.code)
        ))
        selected = st.selectbox("Account", accounts, format_func=lambda a: f"{a.code} · {a.name}")
        normal_debit = selected.account_type in ("asset", "expense")
        running = 0
        rows = []
        for date, memo, debit, credit, transaction_id in ledger_rows(
            db, current_business_id(), selected.code
        ):
            running += debit - credit if normal_debit else credit - debit
            rows.append({
                "Date": date.strftime("%Y-%m-%d %H:%M"), "Details": memo,
                "Debit (₦)": format_naira(debit, kobo=True),
                "Credit (₦)": format_naira(credit, kobo=True),
                "Balance (₦)": format_naira(running, kobo=True),
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.info("No postings in this account yet.")
    finally:
        db.close()
