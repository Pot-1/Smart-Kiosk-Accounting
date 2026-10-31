from uuid import uuid4

import pandas as pd
import streamlit as st
from sqlalchemy import select

from database.database import SessionLocal
from database.models import (
    BusinessMembership,
    Product,
    ReceiptAttachment,
    Transaction,
    User,
)
from inventory.stock_manager import stock_totals_by_product
from utils.currency import format_naira


def current_business_id():
    user_id = st.session_state.get("user_id")
    business_id = st.session_state.get("business_id")
    if not isinstance(user_id, int) or not isinstance(business_id, int):
        raise PermissionError("An authenticated business context is required.")
    return business_id


def action_idempotency_key(action):
    state_key = f"transaction_key_{action}"
    if state_key not in st.session_state:
        st.session_state[state_key] = uuid4().hex
    return st.session_state[state_key]


def rotate_action_idempotency_key(action):
    st.session_state[f"transaction_key_{action}"] = uuid4().hex


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
    business_id = current_business_id()
    stock_totals = stock_totals_by_product(session, business_id)

    rows = []
    for product in active_products(session):
        on_hand, value = stock_totals.get(product.id, (0, 0))
        rows.append({
            "SKU": product.sku,
            "Product": product.name,
            "On hand": on_hand,
            "Unit": product.unit,
            "Selling price": format_naira(product.selling_price_kobo, kobo=True),
            "Reorder at": product.reorder_level,
            "Stock value": format_naira(value, kobo=True),
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
    user_id = st.session_state.get("user_id")
    business_id = current_business_id()
    if not isinstance(user_id, int):
        raise PermissionError("An authenticated business membership is required.")
    db = SessionLocal()
    db.info.update(
        tenant_user_id=user_id,
        tenant_business_id=business_id,
    )
    membership = db.scalar(
        select(BusinessMembership.id).where(
            BusinessMembership.user_id == user_id,
            BusinessMembership.business_id == business_id,
            BusinessMembership.is_active.is_(True),
        )
    )
    user = db.get(User, user_id)
    if membership is None or user is None or not user.is_active:
        db.close()
        raise PermissionError("You are not a member of the active business.")
    return db
