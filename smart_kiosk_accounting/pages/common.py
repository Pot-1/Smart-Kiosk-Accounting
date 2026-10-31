import pandas as pd
import streamlit as st
from sqlalchemy import select

from database.database import SessionLocal
from database.models import Product, ReceiptAttachment, Transaction
from utils.currency import format_naira


def current_business_id():
    return int(st.session_state.get("business_id", 1))


def active_products(session):
    return list(
        session.scalars(
            select(Product).where(
                Product.business_id == current_business_id(),
                Product.active.is_(True),
            ).order_by(Product.name)
        )
    )


def choose_product(products, label="Product"):
    if not products:
        st.info("Add a product in Inventory before recording stock transactions.")
        return None
    return st.selectbox(label, products, format_func=lambda p: f"{p.name} · {p.sku}")


def transactions_frame(session, business_id=None):
    from database.models import Transaction

    business_id = business_id or current_business_id()
    rows = session.scalars(
        select(Transaction).where(Transaction.business_id == business_id)
        .order_by(Transaction.transaction_date.desc()).limit(500)
    )
    return pd.DataFrame(
        [
            {
                "Date": row.transaction_date.strftime("%Y-%m-%d %H:%M"),
                "Reference": row.reference,
                "Type": row.transaction_type.replace("_", " ").title(),
                "Customer / supplier": row.counterparty_name or "—",
                "Method": row.payment_method.title(),
                "Amount": format_naira(row.total_kobo, kobo=True),
                "Status": row.status.title(),
            }
            for row in rows
        ]
    )


def product_stock_frame(session):
    from inventory.stock_manager import stock_quantity, stock_value

    rows = []
    for product in active_products(session):
        on_hand = stock_quantity(session, product.id)
        rows.append({
            "SKU": product.sku,
            "Product": product.name,
            "On hand": on_hand,
            "Unit": product.unit,
            "Selling price": format_naira(product.selling_price_kobo, kobo=True),
            "Reorder at": product.reorder_level,
            "Stock value": format_naira(
                stock_value(session, current_business_id(), product.id), kobo=True
            ),
            "Status": "Restock" if on_hand <= product.reorder_level else "In stock",
        })
    return pd.DataFrame(rows)


def show_receipt_gallery(db, transaction_types, key):
    rows = list(db.scalars(
        select(ReceiptAttachment)
        .join(Transaction, Transaction.id == ReceiptAttachment.transaction_id)
        .where(
            ReceiptAttachment.business_id == current_business_id(),
            Transaction.transaction_type.in_(transaction_types),
        )
        .order_by(ReceiptAttachment.created_at.desc())
        .limit(50)
    ))
    if rows:
        selected = st.selectbox(
            "Saved receipt image", rows,
            format_func=lambda item: (
                f"{item.created_at:%Y-%m-%d} · {item.file_name} · "
                f"{item.transaction_id}"
            ),
            key=key,
        )
        st.image(selected.image_data, caption=selected.file_name, width=500)


def run_action(action, success_message):
    try:
        result = action()
        st.success(f"{success_message} Reference: {result.reference}")
    except (ValueError, ArithmeticError) as exc:
        st.error(str(exc))
    except Exception as exc:
        st.error(f"Could not save this transaction: {exc}")


def session():
    return SessionLocal()
