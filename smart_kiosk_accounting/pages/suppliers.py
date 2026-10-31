import streamlit as st
from sqlalchemy import select

from database.models import Supplier, Transaction
from pages.common import (
    action_idempotency_key,
    current_business_id,
    rotate_action_idempotency_key,
    session,
)
from transactions.service import _party_balance, record_supplier_payment
from utils.currency import format_naira, to_kobo


def render():
    st.title("Suppliers")
    st.caption("Track what you owe suppliers and record the payments you make.")
    db = session()
    try:
        payment_key = action_idempotency_key("supplier_payment")
        suppliers = list(db.scalars(
            select(Supplier).where(Supplier.business_id == current_business_id())
            .order_by(Supplier.name)
        ))
        with st.expander("Add a supplier"):
            with st.form("add_supplier"):
                name_input = st.text_input("Supplier name")
                phone = st.text_input("Phone (optional)")
                added = st.form_submit_button("Add supplier")
            if added:
                try:
                    if not name_input.strip():
                        raise ValueError("Enter a supplier name.")
                    duplicate = db.scalar(
                        select(Supplier.id).where(
                            Supplier.business_id == current_business_id(),
                            Supplier.name == name_input.strip(),
                        )
                    )
                    if duplicate:
                        raise ValueError("This supplier is already on your list.")
                    db.add(Supplier(
                        business_id=current_business_id(), name=name_input.strip(),
                        phone=phone.strip(),
                    ))
                    db.commit()
                    st.success("Supplier added.")
                    st.rerun()
                except ValueError as exc:
                    db.rollback()
                    st.error(str(exc))
        rows = [
            {"Supplier": person.name, "Phone": person.phone or "—",
             "Balance you owe": format_naira(
                 max(0, _party_balance(db, current_business_id(), person.name, "supplier")),
                 kobo=True,
             )}
            for person in suppliers
        ]
        if rows:
            st.dataframe(rows, width="stretch", hide_index=True)
        else:
            st.info("Suppliers are added automatically when you record a purchase.")
        if suppliers:
            with st.form("supplier_payment"):
                name = st.selectbox("Supplier", [person.name for person in suppliers])
                amount = st.number_input("Amount paid (₦)", min_value=0.0, step=500.0)
                method = st.selectbox("Paid from", ["cash", "bank", "pos"])
                submitted = st.form_submit_button("Record supplier payment", type="primary")
            if submitted:
                try:
                    result = record_supplier_payment(
                        db, current_business_id(), name, to_kobo(amount), method,
                        idempotency_key=payment_key,
                    )
                    db.commit()
                    rotate_action_idempotency_key("supplier_payment")
                    st.success(f"Payment recorded: {result.reference}")
                    st.rerun()
                except ValueError as exc:
                    db.rollback()
                    rotate_action_idempotency_key("supplier_payment")
                    st.error(str(exc))
        st.subheader("Credit purchases")
        purchases = db.scalars(
            select(Transaction).where(
                Transaction.business_id == current_business_id(),
                Transaction.transaction_type == "purchase",
                Transaction.credit_amount_kobo > 0,
                Transaction.status == "posted",
            ).order_by(Transaction.transaction_date.desc())
        )
        st.dataframe(
            [{"Date": row.transaction_date.strftime("%Y-%m-%d"),
              "Supplier": row.counterparty_name, "Reference": row.reference,
              "Amount": format_naira(row.credit_amount_kobo, kobo=True)} for row in purchases],
            width="stretch", hide_index=True,
        )
    finally:
        db.close()
