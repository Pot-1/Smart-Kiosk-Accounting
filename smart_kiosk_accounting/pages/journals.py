import pandas as pd
import streamlit as st
from sqlalchemy import select

from accounting.trial_balance import trial_balance_rows
from database.models import Account, JournalEntry, JournalLine
from pages.common import current_business_id, session
from utils.currency import format_naira


def render():
    st.title("Journals")
    st.caption("Each posted transaction creates a balanced debit-and-credit journal automatically.")
    db = session()
    try:
        business_id = current_business_id()
        entries = list(db.scalars(
            select(JournalEntry).where(JournalEntry.business_id == business_id)
            .order_by(JournalEntry.entry_date.desc())
        ))
        choices = {f"{entry.entry_date:%Y-%m-%d %H:%M} · {entry.memo}": entry for entry in entries}
        if not choices:
            st.info("No journal entries yet.")
            return
        selected = st.selectbox("Journal entry", list(choices))
        entry = choices[selected]
        rows = db.execute(
            select(Account.code, Account.name, JournalLine.debit_kobo, JournalLine.credit_kobo)
            .join(JournalLine, JournalLine.account_id == Account.id)
            .where(JournalLine.journal_entry_id == entry.id)
        ).all()
        frame = pd.DataFrame(
            [{"Account": f"{code} · {name}",
              "Debit (₦)": format_naira(debit, kobo=True),
              "Credit (₦)": format_naira(credit, kobo=True)}
             for code, name, debit, credit in rows]
        )
        st.dataframe(frame, width="stretch", hide_index=True)
        st.caption(f"Reference: {entry.transaction.reference if entry.transaction else '—'}")
    finally:
        db.close()
