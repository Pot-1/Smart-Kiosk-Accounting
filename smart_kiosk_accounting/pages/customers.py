import streamlit as st
from sqlalchemy import select

from database.models import Customer, Transaction
from pages.common import (
    action_idempotency_key,
    current_business_id,
    rotate_action_idempotency_key,
    session,
)
from transactions.service import _party_balance, record_customer_payment
from utils.currency import format_naira, to_kobo


def render():
    st.title("Customers")
    st.caption("Money customers owe you and payments you have received.")
    db = session()
    try:
        payment_key = action_idempotency_key("customer_payment")
        customers = list(db.scalars(
            select(Customer).where(Customer.business_id == current_business_id())
            .order_by(Customer.name)
        ))
        with st.expander("Add a customer"):
            with st.form("add_customer"):
                name_input = st.text_input("Customer name")
                phone = st.text_input("Phone (optional)")
                added = st.form_submit_button("Add customer")
            if added:
                try:
                    if not name_input.strip():
                        raise ValueError("Enter a customer name.")
                    duplicate = db.scalar(
                        select(Customer.id).where(
                            Customer.business_id == current_business_id(),
                            Customer.name == name_input.strip(),
                        )
                    )
                    if duplicate:
                        raise ValueError("This customer is already on your list.")
                    db.add(Customer(
                        business_id=current_business_id(), name=name_input.strip(),
                        phone=phone.strip(),
                    ))
                    db.commit()
                    st.success("Customer added.")
                    st.rerun()
                except ValueError as exc:
                    db.rollback()
                    st.error(str(exc))
        rows = [
            {"Customer": person.name, "Phone": person.phone or "—",
             "Balance owed": format_naira(
                 max(0, -_party_balance(db, current_business_id(), person.name, "customer")),
                 kobo=True,
             )}
            for person in customers
        ]
        if rows:
            st.dataframe(rows, width="stretch", hide_index=True)
        else:
            st.info("Customers are added automatically when you make a credit sale.")
        if customers:
            with st.form("customer_payment"):
                name = st.selectbox("Customer", [person.name for person in customers])
                amount = st.number_input("Payment received (₦)", min_value=0.0, step=500.0)
                method = st.selectbox("Received into", ["cash", "bank", "pos"])
                submitted = st.form_submit_button("Record customer payment", type="primary")
            if submitted:
                try:
                    result = record_customer_payment(
                        db, current_business_id(), name, to_kobo(amount), method,
                        idempotency_key=payment_key,
                    )
                    db.commit()
                    rotate_action_idempotency_key("customer_payment")
                    st.success(f"Payment recorded: {result.reference}")
                    st.rerun()
                except ValueError as exc:
                    db.rollback()
                    rotate_action_idempotency_key("customer_payment")
                    st.error(str(exc))
        st.subheader("Credit sales")
        credit_sales = db.scalars(
            select(Transaction).where(
                Transaction.business_id == current_business_id(),
                Transaction.transaction_type == "sale",
                Transaction.credit_amount_kobo > 0,
                Transaction.status == "posted",
            ).order_by(Transaction.transaction_date.desc())
        )
        st.dataframe(
            [{"Date": row.transaction_date.strftime("%Y-%m-%d"),
              "Customer": row.counterparty_name, "Reference": row.reference,
              "Amount": format_naira(row.credit_amount_kobo, kobo=True)} for row in credit_sales],
            width="stretch", hide_index=True,
        )
    finally:
        db.close()
