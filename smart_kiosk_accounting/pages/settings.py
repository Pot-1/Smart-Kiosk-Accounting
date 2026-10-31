import streamlit as st
from sqlalchemy import select

from database.models import Business, User
from pages.common import current_business_id, session
from utils.authentication import hash_password, verify_password


def render():
    st.title("Settings")
    st.caption("Set your business preferences and secure your local account.")
    db = session()
    try:
        business = db.get(Business, current_business_id())
        st.subheader("Business")
        name = st.text_input("Business name", value=business.name)
        method = st.selectbox(
            "How should stock cost be calculated?",
            ["FIFO", "LIFO", "WEIGHTED_AVERAGE"],
            index=["FIFO", "LIFO", "WEIGHTED_AVERAGE"].index(business.inventory_method),
            format_func=lambda x: {
                "FIFO": "FIFO — use the earliest purchase cost first (recommended)",
                "LIFO": "LIFO — use the latest purchase cost first",
                "WEIGHTED_AVERAGE": "Weighted average — blend the cost of all stock",
            }[x],
        )
        if st.button("Save business settings", type="primary"):
            if not name.strip():
                st.error("Business name cannot be empty.")
            else:
                business.name = name.strip()
                business.inventory_method = method
                db.commit()
                st.success("Business settings saved.")

        st.subheader("Change password")
        user = db.get(User, st.session_state.get("user_id"))
        with st.form("password_change"):
            current = st.text_input("Current password", type="password")
            new = st.text_input("New password (at least 10 characters)", type="password")
            confirm = st.text_input("Confirm new password", type="password")
            submitted = st.form_submit_button("Change password")
        if submitted:
            if not user or not verify_password(current, user.password_hash):
                st.error("Current password is incorrect.")
            elif len(new) < 10:
                st.error("Use a password with at least 10 characters.")
            elif new != confirm:
                st.error("The new passwords do not match.")
            else:
                user.password_hash = hash_password(new)
                db.commit()
                st.success("Password updated.")
    finally:
        db.close()
