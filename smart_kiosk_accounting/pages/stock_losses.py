import streamlit as st
from sqlalchemy import select

from database.models import Product, StockAdjustment, StockCount, StockCountLine
from inventory.stock_manager import WEIGHT_UNITS, stock_quantity
from pages.common import (
    action_idempotency_key,
    active_products,
    choose_product,
    current_business_id,
    rotate_action_idempotency_key,
    session,
)
from transactions.service import record_stock_adjustment
from transactions.service import record_stock_count
from utils.currency import format_naira, to_kobo


def render():
    st.title("Stock losses")
    st.caption("Record missing, stolen, spoiled, damaged, or short-delivered goods. Losses are not sales.")
    db = session()
    try:
        adjustment_key = action_idempotency_key("stock_adjustment")
        products = active_products(db)
        with st.form("stock_adjustment"):
            product = choose_product(products)
            fractional = bool(product and product.unit.lower() in WEIGHT_UNITS)
            quantity = st.number_input(
                "Quantity affected",
                min_value=0.001 if fractional else 1,
                value=1.0 if fractional else 1,
                step=0.001 if fractional else 1,
                format="%.3f" if fractional else "%d",
            )
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
                        db, current_business_id(), product, quantity, reason, note,
                        idempotency_key=adjustment_key,
                    )
                    db.commit()
                    rotate_action_idempotency_key("stock_adjustment")
                    st.success(f"Adjustment recorded: {result.reference}")
                except ValueError as exc:
                    db.rollback()
                    rotate_action_idempotency_key("stock_adjustment")
                    st.error(str(exc))

        st.subheader("Physical stock count")
        count_products = st.multiselect(
            "Products to count",
            products,
            format_func=lambda item: f"{item.name} · {item.sku}",
            key="stock_count_products",
        )
        count_key = action_idempotency_key("stock_count")
        with st.form("physical_stock_count"):
            count_reason = st.text_input("Count reason", placeholder="Weekly freezer count")
            counts = []
            for item in count_products:
                st.markdown(
                    f"**{item.name}** · recorded: {stock_quantity(db, item.id)} {item.unit}"
                )
                fractional = item.unit.lower() in WEIGHT_UNITS
                counted = st.number_input(
                    f"Actual quantity · {item.name}",
                    min_value=0.0 if fractional else 0,
                    value=float(stock_quantity(db, item.id)),
                    step=0.001 if fractional else 1,
                    format="%.3f" if fractional else "%d",
                    key=f"count_actual_{item.id}",
                )
                loss_reason = st.selectbox(
                    f"Discrepancy reason · {item.name}",
                    ["missing", "damaged", "spoiled", "stolen", "supplier_shortage"],
                    format_func=lambda value: value.replace("_", " ").title(),
                    key=f"count_reason_{item.id}",
                )
                unit_cost = st.number_input(
                    f"Unit cost for additional stock (₦) · {item.name}",
                    min_value=0.0,
                    step=100.0,
                    key=f"count_unit_cost_{item.id}",
                )
                counts.append(
                    (item, counted, loss_reason, to_kobo(unit_cost))
                )
            count_submitted = st.form_submit_button(
                "Complete stock count", type="primary"
            )
        if count_submitted:
            try:
                result = record_stock_count(
                    db,
                    current_business_id(),
                    counts,
                    count_reason,
                    idempotency_key=count_key,
                )
                db.commit()
                rotate_action_idempotency_key("stock_count")
                st.success(
                    f"Stock count completed: #{result.id}. "
                    "Discrepancies were posted to the inventory ledger."
                )
            except ValueError as exc:
                db.rollback()
                rotate_action_idempotency_key("stock_count")
                st.error(str(exc))

        st.subheader("Stock discrepancy report")
        discrepancies = list(
            db.execute(
                select(StockCountLine, Product, StockCount)
                .join(Product, Product.id == StockCountLine.product_id)
                .join(StockCount, StockCount.id == StockCountLine.stock_count_id)
                .where(
                    StockCountLine.business_id == current_business_id(),
                    StockCountLine.variance != 0,
                )
                .order_by(StockCountLine.created_at.desc())
                .limit(200)
            )
        )
        st.dataframe(
            [
                {
                    "Date": count.created_at.strftime("%Y-%m-%d"),
                    "Product": product.name,
                    "Recorded": line.expected_quantity,
                    "Counted": line.counted_quantity,
                    "Variance": line.variance,
                    "Reason": line.adjustment_reason.replace("_", " ").title(),
                    "Count note": count.reason,
                }
                for line, product, count in discrepancies
            ],
            width="stretch",
            hide_index=True,
        )
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
