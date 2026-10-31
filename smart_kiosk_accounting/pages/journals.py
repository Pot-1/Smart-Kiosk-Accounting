import pandas as pd
import streamlit as st
from sqlalchemy import select

from accounting.trial_balance import trial_balance_rows
from database.models import Account, JournalEntry, JournalLine, Transaction
from pages.common import (
    action_idempotency_key,
    current_business_id,
    rotate_action_idempotency_key,
    session,
)
from transactions.service import reverse_transaction
from utils.currency import format_naira


def render():
    st.title("Journals")
    st.caption("Each posted transaction creates a balanced debit-and-credit journal automatically.")
    db = session()
    try:
        business_id = current_business_id()
        reversal_key = action_idempotency_key("transaction_reversal")
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
        st.divider()
        st.subheader("Reverse a transaction")
        reversible = list(
            db.scalars(
                select(Transaction)
                .where(
                    Transaction.business_id == business_id,
                    Transaction.status == "posted",
                    Transaction.reversal_of_id.is_(None),
                )
                .order_by(Transaction.transaction_date.desc())
            )
        )
        if reversible:
            with st.form("transaction_reversal_form"):
                original = st.selectbox(
                    "Transaction to reverse",
                    reversible,
                    format_func=lambda row: (
                        f"{row.reference} · {row.transaction_type.replace('_', ' ').title()} · "
                        f"{format_naira(row.total_kobo, kobo=True)}"
                    ),
                )
                reason = st.text_input("Reason for reversal")
                submitted = st.form_submit_button(
                    "Post reversal", type="primary"
                )
            if submitted:
                try:
                    reversal = reverse_transaction(
                        db,
                        business_id,
                        original,
                        reason,
                        idempotency_key=reversal_key,
                    )
                    db.commit()
                    rotate_action_idempotency_key("transaction_reversal")
                    st.success(
                        f"Reversal {reversal.reference} posted. "
                        "The original transaction remains in the audit history."
                    )
                except ValueError as exc:
                    db.rollback()
                    rotate_action_idempotency_key("transaction_reversal")
                    st.error(str(exc))
        else:
            st.info("There are no posted transactions available for reversal.")
    finally:
        db.close()
