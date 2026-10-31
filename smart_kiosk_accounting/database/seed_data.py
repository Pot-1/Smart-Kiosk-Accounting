import os

from sqlalchemy import select

from accounting.chart_of_accounts import DEFAULT_ACCOUNTS
from accounting.journal_engine import post_journal
from config import DEFAULT_BUSINESS_NAME, DEMO_MODE, INVENTORY_METHOD
from database.models import (
    Account,
    Business,
    Customer,
    Product,
    ProductCategory,
    Supplier,
    Transaction,
    User,
)
from database.tenant import tenant_system_operation
from transactions.service import (
    record_customer_payment,
    record_purchase,
    record_sale,
    record_stock_adjustment,
    record_supplier_payment,
)
from utils.authentication import hash_password
from utils.helpers import lagos_now
from utils.authorization import seed_role_permissions


def seed_demo_business(session):
    if not DEMO_MODE:
        raise RuntimeError("Demo data can only be seeded when SKA_DEMO_MODE is enabled.")
    with tenant_system_operation(session):
        return _seed_demo_business(session)


def _seed_demo_business(session):
    business = session.scalar(select(Business).order_by(Business.id).limit(1))
    if business is not None:
        return
    method = (
        INVENTORY_METHOD
        if INVENTORY_METHOD in {"FIFO", "WEIGHTED_AVERAGE"}
        else "FIFO"
    )
    business = Business(
        name=DEFAULT_BUSINESS_NAME,
        inventory_method=method,
        business_type="Frozen foods",
        legal_structure="Sole proprietorship",
        onboarding_step=8,
        onboarding_complete=True,
    )
    session.add(business)
    session.flush()
    accounts = {
        code: Account(
            business_id=business.id, code=code, name=name, account_type=kind
        )
        for code, name, kind in DEFAULT_ACCOUNTS
    }
    session.add_all(accounts.values())
    category = ProductCategory(business_id=business.id, name="Frozen food")
    supplier = Supplier(
        business_id=business.id, name="Lagos Poultry Supplier", phone=""
    )
    customer = Customer(
        business_id=business.id, name="Demo Customer", phone=""
    )
    session.add_all([category, supplier, customer])
    session.flush()
    product = Product(
        business_id=business.id,
        category_id=category.id,
        sku="CHK-001",
        name="Whole chicken",
        unit="pieces",
        selling_price_kobo=1_000_000,
        reorder_level=10,
    )
    user = User(
        business_id=business.id,
        username=os.getenv("SKA_ADMIN_USERNAME", "admin"),
        password_hash=hash_password(os.getenv("SKA_ADMIN_PASSWORD", "kiosk1234")),
        full_name="Demo Administrator",
        email=os.getenv("SKA_ADMIN_EMAIL", "admin@example.invalid"),
    )
    session.add_all([product, user])
    session.flush()
    from database.models import RoleAssignment

    business.created_by_user_id = user.id
    session.add(
        RoleAssignment(
            user_id=user.id,
            business_id=business.id,
            username=user.username,
            role="OWNER",
        )
    )
    seed_role_permissions(session, business.id)

    opening = Transaction(
        business_id=business.id,
        reference="OPENING-CAPITAL",
        transaction_type="opening_balance",
        transaction_date=lagos_now(),
        payment_method="cash",
        total_kobo=150_000_000,
        memo="Opening owner capital",
        status="posted",
    )
    session.add(opening)
    session.flush()
    post_journal(
        session, business.id, opening, opening.memo,
        [("1010", 150_000_000, 0), ("3000", 0, 150_000_000)],
    )

    record_purchase(
        session, business.id, [(product, 100, 800_000)],
        supplier.name, payment_method="credit", transport_kobo=1_500_000,
    )
    record_sale(session, business.id, [(product, 5, 1_000_000)], "cash")
    record_sale(
        session, business.id, [(product, 3, 1_000_000)], "credit", customer.name
    )
    record_customer_payment(
        session, business.id, customer.name, 1_000_000, "bank"
    )
    record_supplier_payment(
        session, business.id, supplier.name, 10_000_000, "cash"
    )
    record_stock_adjustment(session, business.id, product, 1, "missing", "Missing during stock count")
    record_stock_adjustment(session, business.id, product, 1, "damaged", "Damaged in storage")
