import pandas as pd
import streamlit as st

from accounting.trial_balance import trial_balance_rows
from pages.common import current_business_id, session
from utils.currency import format_naira


def render():
    st.title("Trial balance")
    st.caption("Checks that total debits equal total credits across all posted accounting entries.")
    db = session()
    try:
        rows = trial_balance_rows(db, current_business_id())
        data = [
            {"Code": code, "Account": name, "Type": kind.title(),
             "Debit (₦)": format_naira(debit, kobo=True),
             "Credit (₦)": format_naira(credit, kobo=True)}
            for code, name, kind, debit, credit in rows if debit or credit
        ]
        total_debit = sum(row[3] for row in rows)
        total_credit = sum(row[4] for row in rows)
        st.dataframe(pd.DataFrame(data), width="stretch", hide_index=True)
        left, right = st.columns(2)
        left.metric("Total debits", format_naira(total_debit, kobo=True))
        right.metric("Total credits", format_naira(total_credit, kobo=True))
        if total_debit == total_credit:
            st.success("The trial balance is balanced.")
        else:
            st.error("The trial balance does not balance. Check the underlying journal entries.")
    finally:
        db.close()
