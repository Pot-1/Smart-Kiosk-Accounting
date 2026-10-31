import streamlit as st
from sqlalchemy import select

from database.models import Product, Transaction, TransactionItem
from pages.common import (
    action_idempotency_key,
    active_products,
    current_business_id,
    rotate_action_idempotency_key,
    session,
    show_receipt_gallery,
)
from reports.pdf_export import create_pdf_report
from inventory.stock_manager import WEIGHT_UNITS
from transactions.service import record_sale, record_sales_return
from utils.currency import format_naira, to_kobo
from utils.receipt_images import optimize_receipt_image


def render():
    st.title("Record a sale")
    st.caption("Choose how the customer paid. Credit sales add to money customers owe you.")
    db = session()
    try:
        sale_key = action_idempotency_key("sale")
        return_key = action_idempotency_key("sales_return")
        receipt = st.session_state.get("last_receipt")
        if receipt:
            st.download_button(
                "Download latest sales receipt (PDF)", receipt["payload"],
                f"receipt-{receipt['reference']}.pdf", "application/pdf",
            )
        products = active_products(db)
        line_count = st.number_input(
            "Different products", min_value=1, max_value=6, value=1, step=1,
            key="sale_line_count",
            help="Add up to six different products to this sale.",
        )
        with st.form("sale_form"):
            customer = st.text_input("Customer name (needed for credit)", value="")
            items = []
            options = [None, *products]
            for index in range(int(line_count)):
                st.markdown(f"**Product {index + 1}**")
                product_col, qty_col, price_col = st.columns([2, 1, 2])
                product = product_col.selectbox(
                    "Product", options,
                    format_func=lambda p: "Choose a product" if p is None else f"{p.name} · {p.sku}",
                    key=f"sale_product_{index}",
                )
                quantity = qty_col.number_input(
                    "Quantity",
                    min_value=0.001 if product and product.unit.lower() in WEIGHT_UNITS else 1,
                    value=1.0 if product and product.unit.lower() in WEIGHT_UNITS else 1,
                    step=0.001 if product and product.unit.lower() in WEIGHT_UNITS else 1,
                    format="%.3f" if product and product.unit.lower() in WEIGHT_UNITS else "%d",
                    key=f"sale_quantity_{index}",
                )
                suggested = product.selling_price_kobo / 100 if product else 0.0
                price = price_col.number_input(
                    "Unit price (₦)", min_value=0.0, value=suggested, step=100.0,
                    key=f"sale_price_{index}_{product.id if product else 'none'}",
                )
                if product is not None:
                    items.append((product, quantity, to_kobo(price)))
            method = st.selectbox("Customer paid by", ["cash", "bank", "pos", "credit", "split"],
                                  format_func=lambda x: {"cash": "Cash", "bank": "Bank transfer",
                                                        "pos": "POS", "credit": "Pay later",
                                                        "split": "Split payment"}[x])
            split_payments = []
            if method == "split":
                st.caption("Enter the amount received by each method. The amounts must equal the sale total.")
                for split_method in ("cash", "bank", "pos", "credit"):
                    split_amount = st.number_input(
                        f"{split_method.title()} portion (₦)",
                        min_value=0.0,
                        step=500.0,
                        key=f"sale_split_{split_method}",
                    )
                    if split_amount > 0:
                        split_payments.append((split_method, to_kobo(split_amount)))
            receipt_upload = st.file_uploader(
                "Receipt photo (optional)", type=["jpg", "jpeg", "png"],
                key="sale_receipt",
            )
            submitted = st.form_submit_button("Save sale", type="primary")
        if submitted:
            if not items:
                st.error("Add stock before recording a sale.")
            elif method == "credit" and not customer.strip():
                st.error("Enter a customer name for a credit sale.")
            else:
                try:
                    result = record_sale(
                        db, current_business_id(), items, method, customer.strip(),
                        optimize_receipt_image(receipt_upload.getvalue()) if receipt_upload else None,
                        receipt_upload.name if receipt_upload else "",
                        split_payments=split_payments,
                        idempotency_key=sale_key,
                    )
                    receipt_rows = [
                        ["Item", "Quantity", "Unit price", "Amount"],
                        *[
                            [
                                item.description,
                                str(item.quantity),
                                format_naira(item.unit_price_kobo, kobo=True),
                                format_naira(item.total_kobo, kobo=True),
                            ]
                            for item in result.items
                        ],
                    ]
                    receipt_rows.extend([
                        ["Reference", result.reference, "Total", format_naira(result.total_kobo, kobo=True)],
                        ["Paid by", method.title(), "Customer", customer.strip() or "Walk-in"],
                    ])
                    st.session_state["last_receipt"] = {
                        "reference": result.reference,
                        "payload": create_pdf_report(
                            st.session_state.get("business_name", "Sales receipt"),
                            receipt_rows,
                        ),
                    }
                    db.commit()
                    rotate_action_idempotency_key("sale")
                    st.success(
                        f"Sale saved. Reference: {result.reference}. "
                        "Stock and cost of goods sold were updated."
                    )
                except ValueError as exc:
                    db.rollback()
                    rotate_action_idempotency_key("sale")
                    st.error(str(exc))
        with st.expander("Saved sales receipt photos"):
            show_receipt_gallery(db, ("sale",), "sales_receipt_gallery")
        st.divider()
        st.subheader("Record a sales return")
        sales = list(db.scalars(
            select(Transaction).where(
                Transaction.business_id == current_business_id(),
                Transaction.transaction_type == "sale",
                Transaction.status == "posted",
            ).order_by(Transaction.transaction_date.desc())
        ))
        if not sales:
            st.info("There are no sales to return yet.")
        else:
            with st.form("sales_return"):
                sale = st.selectbox(
                    "Original sale", sales,
                    format_func=lambda row: (
                        f"{row.reference} · {row.counterparty_name or 'Walk-in'} · "
                        f"{format_naira(row.total_kobo, kobo=True)}"
                    ),
                )
                items = list(db.scalars(
                    select(TransactionItem).where(TransactionItem.transaction_id == sale.id)
                ))
                selected_item = st.selectbox(
                    "Returned product", items,
                    format_func=lambda row: (
                        f"{row.description} · {row.quantity} · "
                        f"{format_naira(row.total_kobo, kobo=True)}"
                    ),
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
                refund = st.selectbox("Refund method", ["cash", "bank", "pos"])
                return_submitted = st.form_submit_button("Save sales return")
            if return_submitted:
                try:
                    result = record_sales_return(
                        db, current_business_id(), sale, selected_item, count, refund, reason,
                        idempotency_key=return_key,
                    )
                    db.commit()
                    rotate_action_idempotency_key("sales_return")
                    st.success(f"Return recorded: {result.reference}")
                except ValueError as exc:
                    db.rollback()
                    rotate_action_idempotency_key("sales_return")
                    st.error(str(exc))
    finally:
        db.close()
