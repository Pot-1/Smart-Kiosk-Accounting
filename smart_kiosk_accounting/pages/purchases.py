from datetime import date

import streamlit as st
from sqlalchemy import select

from database.models import Product, Transaction, TransactionItem
from inventory.stock_manager import WEIGHT_UNITS
from pages.common import (
    action_idempotency_key,
    active_products,
    current_business_id,
    rotate_action_idempotency_key,
    session,
    show_receipt_gallery,
)
from transactions.service import record_purchase, record_purchase_return
from utils.currency import format_naira, to_kobo
from utils.receipt_images import optimize_receipt_image


def render():
    st.title("Record a purchase")
    st.caption("Buying stock increases your goods on hand and records what you paid or still owe.")
    db = session()
    try:
        purchase_key = action_idempotency_key("purchase")
        return_key = action_idempotency_key("purchase_return")
        products = active_products(db)
        line_count = st.number_input(
            "Different products", min_value=1, max_value=6, value=1, step=1,
            key="purchase_line_count",
            help="Add up to six different products to this purchase.",
        )
        with st.form("purchase_form"):
            items = []
            options = [None, *products]
            for index in range(int(line_count)):
                st.markdown(f"**Product {index + 1}**")
                product_col, qty_col, cost_col = st.columns([2, 1, 2])
                product = product_col.selectbox(
                    "Product", options,
                    format_func=lambda p: "Choose a product" if p is None else f"{p.name} · {p.sku}",
                    key=f"purchase_product_{index}",
                )
                quantity = qty_col.number_input(
                    "Quantity",
                    min_value=0.001 if product and product.unit.lower() in WEIGHT_UNITS else 1,
                    value=1.0 if product and product.unit.lower() in WEIGHT_UNITS else 1,
                    step=0.001 if product and product.unit.lower() in WEIGHT_UNITS else 1,
                    format="%.3f" if product and product.unit.lower() in WEIGHT_UNITS else "%d",
                    key=f"purchase_quantity_{index}",
                )
                cost = cost_col.number_input(
                    "Unit cost (₦)", min_value=0.0, step=100.0,
                    key=f"purchase_cost_{index}",
                )
                if product is not None:
                    expires_on = None
                    if st.checkbox(
                        "Track expiry date",
                        key=f"purchase_expiry_enabled_{index}",
                    ):
                        expires_on = st.date_input(
                            "Expiry date",
                            value=date.today(),
                            key=f"purchase_expiry_{index}",
                        )
                    items.append((product, quantity, to_kobo(cost), expires_on))
            supplier = st.text_input("Supplier", value="Lagos Poultry Supplier")
            transport = st.number_input("Transport / loading (₦)", min_value=0.0, step=500.0)
            method = st.selectbox("Paid by", ["credit", "cash", "bank", "pos", "split"],
                                  format_func=lambda x: {"credit": "Pay later (supplier debt)",
                                                        "cash": "Cash", "bank": "Bank transfer",
                                                        "pos": "POS", "split": "Split payment"}[x])
            split_payments = []
            if method == "split":
                st.caption("Enter the amount paid by each method. The amounts must equal the purchase total.")
                for split_method in ("cash", "bank", "pos", "credit"):
                    split_amount = st.number_input(
                        f"{split_method.title()} portion (₦)",
                        min_value=0.0,
                        step=500.0,
                        key=f"purchase_split_{split_method}",
                    )
                    if split_amount > 0:
                        split_payments.append((split_method, to_kobo(split_amount)))
            receipt_upload = st.file_uploader(
                "Receipt photo (optional)", type=["jpg", "jpeg", "png"],
                key="purchase_receipt",
            )
            submitted = st.form_submit_button("Save purchase", type="primary")
        if submitted:
            if not items:
                st.error("Choose at least one product.")
            else:
                try:
                    result = record_purchase(
                        db, current_business_id(), items, supplier,
                        method, to_kobo(transport),
                        optimize_receipt_image(receipt_upload.getvalue()) if receipt_upload else None,
                        receipt_upload.name if receipt_upload else "",
                        split_payments=split_payments,
                        idempotency_key=purchase_key,
                    )
                    db.commit()
                    rotate_action_idempotency_key("purchase")
                    st.success(f"Purchase saved. Reference: {result.reference}")
                except ValueError as exc:
                    db.rollback()
                    rotate_action_idempotency_key("purchase")
                    st.error(str(exc))
        with st.expander("Saved purchase receipt photos"):
            show_receipt_gallery(db, ("purchase",), "purchase_receipt_gallery")
        st.divider()
        st.subheader("Record a purchase return")
        purchases = list(db.scalars(
            select(Transaction).where(
                Transaction.business_id == current_business_id(),
                Transaction.transaction_type == "purchase",
                Transaction.status == "posted",
            ).order_by(Transaction.transaction_date.desc())
        ))
        if not purchases:
            st.info("There are no purchases to return yet.")
        else:
            with st.form("purchase_return"):
                purchase = st.selectbox(
                    "Original purchase", purchases,
                    format_func=lambda row: (
                        f"{row.reference} · {row.counterparty_name} · "
                        f"{format_naira(row.total_kobo, kobo=True)}"
                    ),
                )
                items = list(db.scalars(
                    select(TransactionItem).where(TransactionItem.transaction_id == purchase.id)
                ))
                selected_item = st.selectbox(
                    "Returned product", items,
                    format_func=lambda row: row.description,
                )
                selected_product = db.get(Product, selected_item.product_id)
                fractional = selected_product.unit.lower() in WEIGHT_UNITS
                count = st.number_input(
                    "Quantity returned",
                    min_value=0.001 if fractional else 1,
                    value=1.0 if fractional else 1,
                    step=0.001 if fractional else 1,
                    format="%.3f" if fractional else "%d",
                )
                reason = st.text_input("Reason")
                refund = st.selectbox("Supplier refund method (cash purchase)", ["cash", "bank", "pos"])
                return_submitted = st.form_submit_button("Save purchase return")
            if return_submitted:
                try:
                    result = record_purchase_return(
                        db, current_business_id(), purchase, selected_item,
                        count, reason, refund, idempotency_key=return_key,
                    )
                    db.commit()
                    rotate_action_idempotency_key("purchase_return")
                    st.success(f"Return recorded: {result.reference}")
                except ValueError as exc:
                    db.rollback()
                    rotate_action_idempotency_key("purchase_return")
                    st.error(str(exc))
    finally:
        db.close()
