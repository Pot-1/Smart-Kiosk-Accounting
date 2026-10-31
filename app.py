import streamlit as st
from sqlalchemy import select

from config import APP_NAME
from database.database import SessionLocal, initialize_database
from database.models import Business, User
from pages import (
    cash_bank,
    customers,
    dashboard,
    expenses,
    financial_statements,
    inventory,
    journals,
    ledgers,
    purchases,
    sales,
    settings,
    stock_losses,
    suppliers,
    trial_balance,
)
from utils.authentication import verify_password


st.set_page_config(
    page_title=APP_NAME,
    page_icon="🐔",
    layout="wide",
    initial_sidebar_state="expanded",
)

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


def _login():
    st.title("🐔 Smart Kiosk Accounting")
    st.caption("Simple, trustworthy accounting for your shop.")
    left, form, right = st.columns([1, 1.2, 1])
    with form:
        st.subheader("Sign in")
        with st.form("login"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button(
                "Sign in", type="primary", width="stretch"
            )
        if submitted:
            with SessionLocal() as db:
                user = db.scalar(
                    select(User).where(
                        User.username == username.strip(), User.is_active.is_(True)
                    )
                )
                if user and verify_password(password, user.password_hash):
                    business = db.get(Business, user.business_id)
                    st.session_state.update(
                        authenticated=True,
                        user_id=user.id,
                        business_id=business.id,
                        username=user.username,
                    )
                    st.rerun()
                st.error("Username or password is not correct.")
        st.info("Demo sign-in: `admin` / `kiosk1234`. Change the password in Settings.")


def _navigation():
    simple = st.session_state.get("view_mode", "Simple") == "Simple"
    business_pages = [
        st.Page(dashboard.render, title="Dashboard", url_path="dashboard",
                icon=":material/dashboard:", default=True),
        st.Page(inventory.render, title="Inventory", url_path="inventory",
                icon=":material/inventory_2:"),
        st.Page(purchases.render, title="Purchases", url_path="purchases",
                icon=":material/shopping_bag:"),
        st.Page(sales.render, title="Sales", url_path="sales",
                icon=":material/point_of_sale:"),
        st.Page(expenses.render, title="Expenses", url_path="expenses",
                icon=":material/payments:"),
        st.Page(cash_bank.render, title="Cash & bank", url_path="cash-bank",
                icon=":material/account_balance:"),
        st.Page(customers.render, title="Customers", url_path="customers",
                icon=":material/people:"),
        st.Page(suppliers.render, title="Suppliers", url_path="suppliers",
                icon=":material/local_shipping:"),
        st.Page(stock_losses.render, title="Stock losses", url_path="stock-losses",
                icon=":material/warning:"),
        st.Page(financial_statements.render, title="Reports", url_path="reports",
                icon=":material/monitoring:"),
    ]
    navigation = {"My business": business_pages}
    if not simple:
        navigation["Accountant tools"] = [
            st.Page(journals.render, title="Journals", url_path="journals",
                    icon=":material/menu_book:"),
            st.Page(ledgers.render, title="General ledger", url_path="general-ledger",
                    icon=":material/list_alt:"),
            st.Page(trial_balance.render, title="Trial balance", url_path="trial-balance",
                    icon=":material/balance:"),
        ]
    navigation["Preferences"] = [
        st.Page(settings.render, title="Settings", url_path="settings",
                icon=":material/settings:")
    ]
    page = st.navigation(navigation, position="sidebar")
    st.sidebar.caption(f"Business · {st.session_state.get('business_name', '')}")
    st.sidebar.selectbox(
        "Interface mode",
        ["Simple", "Accountant"],
        key="view_mode",
        help="Simple mode uses everyday descriptions. Accountant mode adds journals, ledgers and trial balance.",
    )
    if st.sidebar.button("Sign out", width="stretch"):
        for key in ("authenticated", "user_id", "business_id", "username", "business_name"):
            st.session_state.pop(key, None)
        st.rerun()
    return page


def main():
    _initialize()
    if not st.session_state.get("authenticated"):
        login_page = st.navigation(
            [st.Page(_login, title="Sign in", url_path="sign-in",
                     icon=":material/lock:")],
            position="hidden",
        )
        login_page.run()
        return
    with SessionLocal() as db:
        business = db.get(Business, st.session_state["business_id"])
        st.session_state["business_name"] = business.name
    st.sidebar.markdown(f"### {st.session_state['business_name']}")
    page = _navigation()
    page.run()


if __name__ == "__main__":
    main()
