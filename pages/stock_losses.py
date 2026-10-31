import streamlit as st
from sqlalchemy import select

from database.models import Product, StockAdjustment
from pages.common import active_products, current_business_id, choose_product, session
from transactions.service import record_stock_adjustment
from utils.currency import format_naira


def render():
    st.title("Stock losses")
    st.caption("Record missing, stolen, spoiled, damaged, or short-delivered goods. Losses are not sales.")
    db = session()
    try:
        products = active_products(db)
        with st.form("stock_adjustment"):
            product = choose_product(products)
            quantity = st.number_input("Quantity affected", min_value=1, value=1, step=1)
            reason = st.selectbox(
                "Reason",
                ["missing", "stolen", "spoiled", "damaged", "supplier_shortage"],
                format_func=lambda x: x.replace("_", " ").title(),
            )
            note = st.text_input("What happened?")
            submitted = st.form_submit_button("Record stock loss", type="primary")
        if submitted:
            if product is None:
                st.error("Add a product first.")
            else:
                try:
                    result = record_stock_adjustment(
                        db, current_business_id(), product, int(quantity), reason, note
                    )
                    db.commit()
                    st.success(f"Adjustment recorded: {result.reference}")
                except ValueError as exc:
                    db.rollback()
                    st.error(str(exc))
        st.subheader("Adjustment history")
        entries = list(db.scalars(
            select(StockAdjustment).where(
                StockAdjustment.business_id == current_business_id()
            ).order_by(StockAdjustment.id.desc()).limit(200)
        ))
        st.dataframe(
            [{"Reason": row.reason.replace("_", " ").title(),
              "Product": db.get(Product, row.product_id).name,
              "Quantity": row.quantity,
              "Cost impact": format_naira(
                  row.total_cost_kobo or row.unit_cost_kobo * row.quantity, kobo=True
              ),
              "Notes": row.notes or "—"} for row in entries],
            width="stretch", hide_index=True,
        )
    finally:
        db.close()
