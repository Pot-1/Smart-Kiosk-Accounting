import streamlit as st
import pandas as pd
from sqlalchemy import func, select

from database.models import Account, BankStatement, JournalLine
from pages.common import current_business_id, transactions_frame
from pages.common import session
from reports.bank_reconciliation import bank_match_candidates, import_bank_rows
from transactions.service import record_owner_funds
from utils.currency import format_naira, to_kobo


def _balance(db, code):
    account = db.scalar(
        select(Account).where(
            Account.business_id == current_business_id(), Account.code == code
        )
    )
    if not account:
        return 0
    debit, credit = db.execute(
        select(
            func.coalesce(func.sum(JournalLine.debit_kobo), 0),
            func.coalesce(func.sum(JournalLine.credit_kobo), 0),
        ).where(JournalLine.account_id == account.id)
    ).one()
    return int(debit - credit)


def render():
    st.title("Cash & bank")
    st.caption("Balances update automatically whenever you post a sale, payment, purchase, or expense.")
    db = session()
    try:
        left, right = st.columns(2)
        left.metric("Cash in shop", format_naira(_balance(db, "1010"), kobo=True))
        right.metric("Bank and POS", format_naira(_balance(db, "1020"), kobo=True))
        with st.expander("Record owner money in or out"):
            with st.form("owner_funds"):
                direction = st.selectbox("Transaction", ["capital", "drawing"],
                                         format_func=lambda x: "Owner adds money" if x == "capital" else "Owner takes money")
                amount = st.number_input("Amount (₦)", min_value=0.0, step=500.0)
                method = st.selectbox("Cash or account", ["cash", "bank"])
                note = st.text_input("Note (optional)")
                submitted = st.form_submit_button("Record")
            if submitted:
                try:
                    result = record_owner_funds(
                        db, current_business_id(), to_kobo(amount), direction, method, note
                    )
                    db.commit()
                    st.success(f"Saved: {result.reference}")
                    st.rerun()
                except ValueError as exc:
                    db.rollback()
                    st.error(str(exc))
        st.subheader("Recent money movements")
        frame = transactions_frame(db)
        if not frame.empty:
            frame = frame[frame["Method"].str.lower().isin(["cash", "bank", "pos", "transfer"])]
            st.dataframe(frame, width="stretch", hide_index=True)
        else:
            st.info("No transactions have been recorded.")
        st.divider()
        st.subheader("Bank reconciliation")
        st.caption("Import a bank statement with Date, Description, and Amount columns. Use positive amounts for money in and negative for money out.")
        upload = st.file_uploader("Bank statement (CSV or Excel)", type=["csv", "xlsx", "xls"])
        if upload is not None and st.button("Import statement"):
            try:
                imported_frame = (
                    pd.read_csv(upload) if upload.name.lower().endswith(".csv")
                    else pd.read_excel(upload)
                )
                columns = {str(column).strip().lower(): column for column in imported_frame.columns}
                if not {"date", "description", "amount"}.issubset(columns):
                    raise ValueError("Your file must have Date, Description, and Amount columns.")
                rows = []
                for _, row in imported_frame.iterrows():
                    rows.append({
                        "date": pd.to_datetime(row[columns["date"]]).to_pydatetime().replace(tzinfo=None),
                        "description": str(row[columns["description"]]).strip(),
                        "amount_kobo": to_kobo(row[columns["amount"]]),
                    })
                count = import_bank_rows(db, current_business_id(), rows)
                db.commit()
                st.success(f"Imported {count} new bank statement row(s).")
                st.rerun()
            except (ValueError, KeyError, TypeError) as exc:
                db.rollback()
                st.error(f"Could not import statement: {exc}")

        unmatched = list(db.scalars(
            select(BankStatement).where(
                BankStatement.business_id == current_business_id(),
                BankStatement.matched_transaction_id.is_(None),
            ).order_by(BankStatement.transaction_date.desc())
        ))
        matched_count = db.scalar(
            select(func.count()).select_from(BankStatement).where(
                BankStatement.business_id == current_business_id(),
                BankStatement.matched_transaction_id.is_not(None),
            )
        ) or 0
        st.caption(f"{matched_count} statement row(s) reconciled · {len(unmatched)} to review")
        if unmatched:
            statement = st.selectbox(
                "Statement row", unmatched,
                format_func=lambda row: (
                    f"{row.transaction_date:%Y-%m-%d} · {row.description} · "
                    f"{format_naira(row.amount_kobo, kobo=True)}"
                ),
            )
            candidates = bank_match_candidates(
                db, current_business_id(), statement.amount_kobo
            )
            if candidates:
                chosen = st.selectbox(
                    "Matching transaction", candidates,
                    format_func=lambda pair: (
                        f"{pair[0].reference} · {pair[0].memo} · "
                        f"{pair[1]:%Y-%m-%d}"
                    ),
                )
                if st.button("Match statement row"):
                    statement.matched_transaction_id = chosen[0].id
                    db.commit()
                    st.success("Bank statement row reconciled.")
                    st.rerun()
            else:
                st.info("No bank-book entry has this exact amount. You can reconcile it after recording the transaction.")
        st.caption("Cash is account 1010; bank and POS are account 1020.")
    finally:
        db.close()
