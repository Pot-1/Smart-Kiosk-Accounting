import logging
import smtplib
from urllib.parse import quote
from zoneinfo import ZoneInfo

import streamlit as st
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from config import PUBLIC_APP_URL, TIMEZONE
from database.database import SessionLocal
from database.models import Business, User
from utils.auth_service import (
    AuthenticationError,
    authenticate_user,
    create_auth_session,
    create_password_reset,
    register_user,
    reset_password,
    send_password_reset_email,
)

logger = logging.getLogger(__name__)
BUSINESS_TYPES = (
    "Retail / shop",
    "Frozen foods",
    "Food and restaurant",
    "Wholesale and distribution",
    "Fashion and clothing",
    "Pharmacy and healthcare",
    "Services",
    "Other",
)
LEGAL_STRUCTURES = (
    "Sole proprietorship",
    "Partnership",
    "Limited liability company",
    "Other",
)


def _activate_session(user, business, role, raw_token: str, auth_session) -> None:
    st.session_state.update(
        authenticated=True,
        user_id=user.id,
        business_id=business.id,
        username=user.username,
        business_name=business.name,
        role=role,
        auth_token=raw_token,
        auth_expires_at=auth_session.expires_at.isoformat(),
    )


def _set_cookie(cookie_manager, raw_token: str, auth_session) -> None:
    expiry = (
        auth_session.expires_at.replace(tzinfo=ZoneInfo(TIMEZONE))
        if auth_session.remember_device else None
    )
    cookie_manager.set(
        "ska_auth",
        raw_token,
        expires_at=expiry,
        secure=PUBLIC_APP_URL.startswith("https://"),
        same_site="strict",
    )


def render_login(cookie_manager, register_page, forgot_page) -> None:
    st.title("🐔 Smart Kiosk Accounting")
    st.caption("Secure access to your business accounts.")
    _, form, _ = st.columns([1, 1.2, 1])
    with form:
        st.subheader("Sign in")
        with st.form("login"):
            identifier = st.text_input("Username or email", autocomplete="username")
            password = st.text_input(
                "Password", type="password", autocomplete="current-password"
            )
            remember = st.checkbox("Remember this device for 30 days")
            submitted = st.form_submit_button(
                "Sign in", type="primary", width="stretch"
            )
        if submitted:
            with st.spinner("Signing you in..."):
                with SessionLocal() as db:
                    db.info["system_operation"] = True
                    try:
                        user, role, business_id = authenticate_user(
                            db, identifier, password
                        )
                    except AuthenticationError as exc:
                        db.commit()
                        st.error(str(exc))
                        return
                    raw_token, auth_session = create_auth_session(
                        db, user, remember, business_id
                    )
                    business = db.get(Business, business_id)
                    db.commit()
                _activate_session(user, business, role, raw_token, auth_session)
                _set_cookie(cookie_manager, raw_token, auth_session)
                st.rerun()

        st.page_link(register_page, label="Create account", width="stretch")
        st.page_link(forgot_page, label="Forgot password?", width="stretch")


def render_registration(cookie_manager, sign_in_page) -> None:
    st.title("Create your account")
    st.caption("Set up your login and business profile.")
    _, form, _ = st.columns([1, 1.5, 1])
    with form:
        with st.form("registration"):
            st.subheader("Your details")
            full_name = st.text_input("Full name", max_chars=160)
            email = st.text_input("Email address", autocomplete="email")
            phone = st.text_input("Phone number", placeholder="+234 801 234 5678")
            username = st.text_input("Username", autocomplete="username")
            password = st.text_input(
                "Password (at least 10 characters)",
                type="password",
                autocomplete="new-password",
            )
            confirm = st.text_input(
                "Confirm password", type="password", autocomplete="new-password"
            )
            st.subheader("Your business")
            business_name = st.text_input("Business name", max_chars=160)
            business_type = st.selectbox("Business type", BUSINESS_TYPES)
            location = st.text_input("Business location", max_chars=200)
            legal_structure = st.selectbox(
                "Business legal structure", LEGAL_STRUCTURES
            )
            submitted = st.form_submit_button(
                "Create account and business",
                type="primary",
                width="stretch",
            )
        if submitted:
            if password != confirm:
                st.error("The passwords do not match.")
                return
            with st.spinner("Creating your account and business..."):
                with SessionLocal() as db:
                    db.info["system_operation"] = True
                    try:
                        user, business = register_user(
                            db,
                            full_name=full_name,
                            email=email,
                            phone=phone,
                            username=username,
                            password=password,
                            business_name=business_name,
                            business_type=business_type,
                            location=location,
                            legal_structure=legal_structure,
                        )
                        raw_token, auth_session = create_auth_session(
                            db, user, remember_device=False
                        )
                        db.commit()
                    except (ValueError, IntegrityError) as exc:
                        db.rollback()
                        st.error(
                            "Those details could not be registered. Check for an "
                            "existing email address."
                            if isinstance(exc, IntegrityError) else str(exc)
                        )
                        return
            _activate_session(user, business, "OWNER", raw_token, auth_session)
            _set_cookie(cookie_manager, raw_token, auth_session)
            st.rerun()
        st.page_link(sign_in_page, label="Already have an account? Sign in")


def render_forgot_password(sign_in_page) -> None:
    st.title("Reset your password")
    st.caption(
        "Enter the email address on your account. If it is registered, "
        "we will send a reset link."
    )
    with st.form("forgot_password"):
        email = st.text_input("Email address", autocomplete="email")
        submitted = st.form_submit_button("Send reset link", type="primary")
    if submitted:
        with st.spinner("Preparing password reset..."):
            with SessionLocal() as db:
                token = create_password_reset(db, email)
                db.commit()
        if token:
            reset_link = (
                f"{PUBLIC_APP_URL}/?reset_token={quote(token, safe='')}"
            )
            try:
                with SessionLocal() as db:
                    db.info["system_operation"] = True
                    user = db.scalar(
                        select(User).where(User.email == email.strip().casefold())
                    )
                    destination = user.email if user else email
                send_password_reset_email(destination, reset_link)
            except (OSError, RuntimeError, ValueError, smtplib.SMTPException):
                logger.exception("Password reset email delivery failed")
        st.success(
            "If an account matches that email address, password-reset instructions "
            "will be sent. Check your inbox and spam folder."
        )
    st.page_link(sign_in_page, label="Back to sign in")


def render_reset_password(cookie_manager, forgot_page, sign_in_page) -> None:
    token = st.query_params.get("reset_token", st.query_params.get("token", ""))
    st.title("Choose a new password")
    if not token:
        st.info("Open the password-reset link from your email.")
        st.page_link(forgot_page, label="Request another reset link")
        return
    with st.form("reset_password"):
        password = st.text_input(
            "New password (at least 10 characters)",
            type="password",
            autocomplete="new-password",
        )
        confirm = st.text_input(
            "Confirm new password", type="password", autocomplete="new-password"
        )
        submitted = st.form_submit_button("Update password", type="primary")
    if submitted:
        if password != confirm:
            st.error("The passwords do not match.")
            return
        with SessionLocal() as db:
            try:
                reset_password(db, token, password)
                db.commit()
            except ValueError as exc:
                db.rollback()
                st.error(str(exc))
                return
        for key in (
            "authenticated", "user_id", "business_id", "username",
            "business_name", "role", "auth_token", "auth_expires_at",
        ):
            st.session_state.pop(key, None)
        cookie_manager.delete("ska_auth")
        st.query_params.clear()
        st.success("Password updated. Sign in with your new password.")
        st.page_link(sign_in_page, label="Sign in")
