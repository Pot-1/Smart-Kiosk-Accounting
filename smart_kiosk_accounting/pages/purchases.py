import streamlit as st
from sqlalchemy import select

from database.models import Product, Transaction, TransactionItem
from pages.common import active_products, current_business_id, session, show_receipt_gallery
from transactions.service import record_purchase, record_purchase_return
from utils.currency import format_naira, to_kobo
from utils.receipt_images import optimize_receipt_image


def render():
    st.title("Record a purchase")
    st.caption("Buying stock increases your goods on hand and records what you paid or still owe.")
    db = session()
    try:
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
                    "Quantity", min_value=1, value=1, step=1,
                    key=f"purchase_quantity_{index}",
                )
                cost = cost_col.number_input(
                    "Unit cost (₦)", min_value=0.0, step=100.0,
                    key=f"purchase_cost_{index}",
                )
                if product is not None:
                    items.append((product, int(quantity), to_kobo(cost)))
            supplier = st.text_input("Supplier", value="Lagos Poultry Supplier")
            transport = st.number_input("Transport / loading (₦)", min_value=0.0, step=500.0)
            method = st.selectbox("Paid by", ["credit", "cash", "bank", "pos"],
                                  format_func=lambda x: {"credit": "Pay later (supplier debt)",
                                                        "cash": "Cash", "bank": "Bank transfer",
                                                        "pos": "POS"}[x])
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
                    )
                    db.commit()
                    st.success(f"Purchase saved. Reference: {result.reference}")
                except ValueError as exc:
                    db.rollback()
                    st.error(str(exc))
        with st.expander("Saved purchase receipt photos"):
            show_receipt_gallery(db, ("purchase",), "purchase_receipt_gallery")
        st.divider()
        st.subheader("Record a purchase return")
        purchases = list(db.scalars(
            select(Transaction).where(
                Transaction.business_id == current_business_id(),
                Transaction.transaction_type == "purchase",
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
                count = st.number_input("Quantity returned", min_value=1, value=1, step=1)
                reason = st.text_input("Reason")
                refund = st.selectbox("Supplier refund method (cash purchase)", ["cash", "bank", "pos"])
                return_submitted = st.form_submit_button("Save purchase return")
            if return_submitted:
                product = db.get(Product, selected_item.product_id)
                try:
                    result = record_purchase_return(
                        db, current_business_id(), purchase, product,
                        int(count), reason, refund,
                    )
                    db.commit()
                    st.success(f"Return recorded: {result.reference}")
                except ValueError as exc:
                    db.rollback()
                    st.error(str(exc))
    finally:
        db.close()
