import streamlit as st

from pages.common import (
    action_idempotency_key,
    current_business_id,
    rotate_action_idempotency_key,
    session,
    show_receipt_gallery,
)
from transactions.service import record_expense
from utils.currency import to_kobo
from utils.receipt_images import optimize_receipt_image


def render():
    st.title("Expenses")
    st.caption("Record money spent by your business, such as electricity, rent, or market fees.")
    db = session()
    try:
        expense_key = action_idempotency_key("expense")
        with st.form("expense_form"):
            category = st.selectbox(
                "What was the money spent on?",
                ["Electricity", "Rent", "Transport", "Market fees", "Packaging", "Repairs", "Other"],
            )
            amount = st.number_input("Amount (₦)", min_value=0.0, step=500.0)
            method = st.selectbox("Paid by", ["cash", "bank", "credit"],
                                  format_func=lambda x: {"cash": "Cash", "bank": "Bank transfer",
                                                        "credit": "Pay later"}[x])
            supplier = st.text_input("Who do you owe? (required for 'Pay later')")
            note = st.text_input("Note (optional)")
            receipt_upload = st.file_uploader(
                "Receipt photo (optional)", type=["jpg", "jpeg", "png"],
                key="expense_receipt",
            )
            submitted = st.form_submit_button("Save expense", type="primary")
        if submitted:
            try:
                result = record_expense(
                    db, current_business_id(), category, to_kobo(amount),
                    method, note, supplier,
                    optimize_receipt_image(receipt_upload.getvalue()) if receipt_upload else None,
                    receipt_upload.name if receipt_upload else "",
                    idempotency_key=expense_key,
                )
                db.commit()
                rotate_action_idempotency_key("expense")
                st.success(f"Expense saved. Reference: {result.reference}")
            except ValueError as exc:
                db.rollback()
                rotate_action_idempotency_key("expense")
                st.error(str(exc))
        with st.expander("Saved expense receipt photos"):
            show_receipt_gallery(db, ("expense",), "expense_receipt_gallery")
    finally:
        db.close()
