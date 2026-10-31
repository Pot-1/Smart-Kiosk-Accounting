import hashlib

import streamlit as st
from sqlalchemy import select

from config import APP_NAME
from database.database import SessionLocal, initialize_database
from database.models import AuditLog, AuthSession, Business, BusinessMembership, User
from pages import (
    authentication,
    cash_bank,
    customers,
    dashboard,
    expenses,
    financial_statements,
    inventory,
    journals,
    ledgers,
    onboarding,
    purchases,
    sales,
    settings,
    stock_losses,
    suppliers,
    trial_balance,
)
from utils.auth_service import create_auth_session, resolve_auth_session, revoke_auth_session
from utils.authorization import user_can
from utils.helpers import lagos_now

st.set_page_config(
    page_title=APP_NAME,
    page_icon="🐔",
    layout="wide",
    initial_sidebar_state="expanded",
)

try:
    from importlib import import_module

    stx = import_module("extra_streamlit_components")
    COOKIE_MANAGER = stx.CookieManager(key="ska_auth_cookie_manager")
except ModuleNotFoundError:
    class _SessionCookieManager:
        """Keep cookie values for the active Streamlit session if the optional
        browser-cookie component is not installed.
        """

        def get(self, name):
            return st.session_state.get(f"_cookie_{name}")

        def set(self, name, value, *args, **kwargs):
            st.session_state[f"_cookie_{name}"] = value

        def delete(self, name):
            st.session_state.pop(f"_cookie_{name}", None)

    COOKIE_MANAGER = _SessionCookieManager()

st.markdown(
    """
    <style>
    :root { color-scheme: dark; }
    .stApp { background: #101725; }
    [data-testid="stSidebar"] { background: #0b1220; }
    [data-testid="stMetric"] {
      background: #1b2636; border: 1px solid #2d3b50;
      border-radius: 12px; padding: 16px;
    }
    div[data-testid="stForm"] {
      background: #151f2e; border: 1px solid #2d3b50;
      border-radius: 12px; padding: 18px;
    }
    .block-container { padding-top: 2rem; }
    @media (max-width: 700px) {
      .block-container { padding: 1rem 0.75rem 2rem; }
      [data-testid="stMetric"] { padding: 10px; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def _initialize():
    initialize_database()
    return True


def _clear_auth_state() -> None:
    for key in (
        "authenticated", "user_id", "business_id", "username", "business_name",
        "role", "auth_token", "auth_expires_at", "force_logout",
    ):
        st.session_state.pop(key, None)


def _restore_session(raw_token: str) -> bool:
    with SessionLocal() as db:
        resolved = resolve_auth_session(db, raw_token)
        if resolved is None:
            return False
        user, business, role, auth_session = resolved
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
        return True


def _login():
    authentication.render_login(COOKIE_MANAGER, AUTH_REGISTER, AUTH_FORGOT)


def _register():
    authentication.render_registration(COOKIE_MANAGER, AUTH_SIGN_IN)


def _forgot_password():
    authentication.render_forgot_password(AUTH_SIGN_IN)


def _reset_password():
    authentication.render_reset_password(
        COOKIE_MANAGER, AUTH_FORGOT, AUTH_SIGN_IN
    )


AUTH_SIGN_IN = st.Page(_login, title="Sign in", url_path="sign-in")
AUTH_REGISTER = st.Page(_register, title="Create account", url_path="register")
AUTH_FORGOT = st.Page(
    _forgot_password, title="Forgot password", url_path="forgot-password"
)


def _authentication_navigation():
    global AUTH_SIGN_IN
    reset_requested = bool(st.query_params.get("reset_token"))
    AUTH_SIGN_IN = st.Page(
        _login, title="Sign in", url_path="sign-in", default=not reset_requested
    )
    reset_page = st.Page(
        _reset_password,
        title="Reset password",
        url_path="reset-password",
        default=reset_requested,
    )
    page = st.navigation(
        [
            AUTH_SIGN_IN,
            AUTH_REGISTER,
            AUTH_FORGOT,
            reset_page,
        ],
        position="hidden",
    )
    page.run()


def _navigation():
    user_id = st.session_state["user_id"]
    business_id = st.session_state["business_id"]
    with SessionLocal() as db:
        db.info["system_operation"] = True
        memberships = list(
            db.execute(
                select(BusinessMembership, Business)
                .join(Business, Business.id == BusinessMembership.business_id)
                .where(
                    BusinessMembership.user_id == user_id,
                    BusinessMembership.is_active.is_(True),
                )
                .order_by(Business.name)
            )
        )
        business_labels = {
            membership.business_id: business.name
            for membership, business in memberships
        }
        membership_roles = {
            membership.business_id: membership.role
            for membership, _business in memberships
        }
        permissions = {
            permission: user_can(db, user_id, business_id, permission)
            for permission in (
                "dashboard", "inventory", "purchases", "sales", "expenses",
                "cash_bank", "customers", "suppliers", "stock_losses", "reports",
                "journals", "ledgers", "trial_balance", "settings",
            )
        }
        business = db.get(Business, business_id)
        business_name = business.name if business else ""
        onboarding_complete = business.onboarding_complete if business else False

    if not business_labels or business_id not in business_labels:
        _clear_auth_state()
        COOKIE_MANAGER.delete("ska_auth")
        st.rerun()

    if len(business_labels) > 1:
        selected_business_id = st.sidebar.selectbox(
            "Active business",
            list(business_labels),
            index=list(business_labels).index(business_id),
            format_func=lambda value: business_labels[value],
            key="active_business_selector",
        )
        if selected_business_id != business_id:
            with SessionLocal() as db:
                db.info["system_operation"] = True
                token_hash = hashlib.sha256(
                    st.session_state["auth_token"].encode()
                ).hexdigest()
                previous_session = db.scalar(
                    select(AuthSession).where(AuthSession.token_hash == token_hash)
                )
                if previous_session is not None:
                    previous_session.revoked_at = lagos_now()
                raw_token, auth_session = create_auth_session(
                    db,
                    db.get(User, user_id),
                    bool(previous_session and previous_session.remember_device),
                    selected_business_id,
                )
                db.add(
                    AuditLog(
                        business_id=selected_business_id,
                        user_id=user_id,
                        action="business_switched",
                        details=f"Switched from business {business_id}.",
                        previous_value=str(business_id),
                        new_value=str(selected_business_id),
                    )
                )
                db.commit()
            st.session_state.update(
                business_id=selected_business_id,
                business_name=business_labels[selected_business_id],
                role=membership_roles[selected_business_id],
                auth_token=raw_token,
                auth_expires_at=auth_session.expires_at.isoformat(),
            )
            from pages.authentication import _set_cookie

            _set_cookie(COOKIE_MANAGER, raw_token, auth_session)
            st.rerun()

    st.session_state["business_name"] = business_name
    st.sidebar.markdown(f"### {business_name}")
    if not onboarding_complete:
        pages = [
            st.Page(
                onboarding.render,
                title="Business setup",
                url_path="onboarding",
                icon=":material/checklist:",
                default=True,
            )
        ]
        if permissions["settings"]:
            pages.append(
                st.Page(
                    settings.render,
                    title="Settings",
                    url_path="settings",
                    icon=":material/settings:",
                )
            )
        page = st.navigation({"Get started": pages}, position="sidebar")
    else:
        simple = st.session_state.get("view_mode", "Simple") == "Simple"
        page_map = (
            ("dashboard", dashboard.render, "Dashboard", "dashboard", ":material/dashboard:"),
            ("inventory", inventory.render, "Inventory", "inventory", ":material/inventory_2:"),
            ("purchases", purchases.render, "Purchases", "purchases", ":material/shopping_bag:"),
            ("sales", sales.render, "Sales", "sales", ":material/point_of_sale:"),
            ("expenses", expenses.render, "Expenses", "expenses", ":material/payments:"),
            ("cash_bank", cash_bank.render, "Cash & bank", "cash-bank", ":material/account_balance:"),
            ("customers", customers.render, "Customers", "customers", ":material/people:"),
            ("suppliers", suppliers.render, "Suppliers", "suppliers", ":material/local_shipping:"),
            ("stock_losses", stock_losses.render, "Stock losses", "stock-losses", ":material/warning:"),
            ("reports", financial_statements.render, "Reports", "reports", ":material/monitoring:"),
        )
        business_pages = [
            st.Page(render, title=title, url_path=path, icon=icon, default=path == "dashboard")
            for permission, render, title, path, icon in page_map
            if permissions[permission]
        ]
        navigation = {}
        if business_pages:
            navigation["My business"] = business_pages
        accountant_pages = []
        if not simple:
            for permission, render, title, path, icon in (
                ("journals", journals.render, "Journals", "journals", ":material/menu_book:"),
                ("ledgers", ledgers.render, "General ledger", "general-ledger", ":material/list_alt:"),
                ("trial_balance", trial_balance.render, "Trial balance", "trial-balance", ":material/balance:"),
            ):
                if permissions[permission]:
                    accountant_pages.append(
                        st.Page(render, title=title, url_path=path, icon=icon)
                    )
        if accountant_pages:
            navigation["Accountant tools"] = accountant_pages
        if permissions["settings"]:
            navigation["Preferences"] = [
                st.Page(
                    settings.render, title="Settings", url_path="settings",
                    icon=":material/settings:",
                )
            ]
        if not navigation:
            navigation = {
                "Access": [
                    st.Page(
                        _no_access, title="No access", url_path="no-access",
                        default=True,
                    )
                ]
            }
        page = st.navigation(navigation, position="sidebar")
        st.sidebar.selectbox(
            "Interface mode",
            ["Simple", "Accountant"],
            key="view_mode",
            help="Simple mode uses everyday descriptions. Accountant mode adds "
            "journals, ledgers and trial balance.",
        )

    if st.sidebar.button("Sign out", width="stretch"):
        with SessionLocal() as db:
            db.info["system_operation"] = True
            token_hash = hashlib.sha256(
                st.session_state.get("auth_token", "").encode()
            ).hexdigest()
            current_session = db.scalar(
                select(AuthSession).where(AuthSession.token_hash == token_hash)
            )
            if current_session is not None:
                db.add(
                    AuditLog(
                        business_id=current_session.business_id,
                        user_id=current_session.user_id,
                        action="logout",
                        details="User signed out.",
                    )
                )
            revoke_auth_session(db, st.session_state.get("auth_token", ""))
            db.commit()
        COOKIE_MANAGER.delete("ska_auth")
        _clear_auth_state()
        st.rerun()
    return page


def _no_access():
    st.error("Your role does not currently have access to any application areas.")
    st.info("Ask your business owner to review your role permissions.")


def main():
    _initialize()
    if st.session_state.pop("force_logout", False):
        COOKIE_MANAGER.delete("ska_auth")
        _clear_auth_state()

    if st.query_params.get("reset_token"):
        _authentication_navigation()
        return

    token = st.session_state.get("auth_token") or COOKIE_MANAGER.get("ska_auth")
    if token and not _restore_session(token):
        COOKIE_MANAGER.delete("ska_auth")
        _clear_auth_state()

    if not st.session_state.get("authenticated"):
        _authentication_navigation()
        return
    if not st.session_state.get("auth_token"):
        _clear_auth_state()
        _authentication_navigation()
        return
    page = _navigation()
    page.run()


if __name__ == "__main__":
    main()
