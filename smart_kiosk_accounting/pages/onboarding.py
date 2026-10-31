import streamlit as st
from sqlalchemy import select

from accounting.journal_engine import post_journal
from database.tenant import require_business_access
from database.models import (
    Business,
    Product,
    ProductCategory,
    Supplier,
    Transaction,
)
from inventory.stock_manager import stock_quantity
from inventory.stock_manager import WEIGHT_UNITS
from pages.common import (
    action_idempotency_key,
    current_business_id,
    rotate_action_idempotency_key,
    session,
)
from transactions.service import record_purchase
from utils.currency import to_kobo
from utils.helpers import lagos_now, new_reference

STEP_TITLES = (
    "Business details",
    "Business type",
    "Opening capital",
    "Opening cash and bank balances",
    "Add products",
    "Enter opening stock",
    "Add suppliers",
    "Complete setup",
)


def _advance(db, business: Business, step: int) -> None:
    business.onboarding_step = step
    db.commit()
    st.rerun()


def _move_buttons(
    business: Business, *, optional=True, continue_label="Save and continue"
) -> tuple[bool, bool, bool]:
    back, continue_col, skip = st.columns(3)
    go_back = back.form_submit_button("Back") if business.onboarding_step > 1 else False
    go_next = continue_col.form_submit_button(
        continue_label, type="primary"
    )
    skip_step = (
        skip.form_submit_button("Skip this step")
        if optional and business.onboarding_step not in (1, 8)
        else False
    )
    return go_back, go_next, skip_step


def _complete_setup(db, business: Business) -> None:
    business.onboarding_complete = True
    business.onboarding_step = 8
    db.commit()
    st.success("Business setup complete. Your dashboard is ready.")
    st.rerun()


def render() -> None:
    st.title("Set up your business")
    st.caption("You can skip optional steps and finish setup later.")
    db = session()
    try:
        business_id = current_business_id()
        try:
            require_business_access(db, business_id, "business_settings")
        except PermissionError as exc:
            st.error(str(exc))
            return
        business = db.get(Business, business_id)
        if business is None:
            st.error("The business account could not be found.")
            return
        step = min(max(business.onboarding_step, 1), 8)
        st.progress(step / 8, text=f"Step {step} of 8 · {STEP_TITLES[step - 1]}")
        if step == 1:
            with st.form("onboarding_business_details"):
                name = st.text_input("Business name", value=business.name)
                location = st.text_input("Business location", value=business.location)
                legal = st.selectbox(
                    "Legal structure",
                    (
                        "Sole proprietorship",
                        "Partnership",
                        "Limited liability company",
                        "Other",
                    ),
                    index=(
                        (
                            "Sole proprietorship",
                            "Partnership",
                            "Limited liability company",
                            "Other",
                        ).index(business.legal_structure)
                        if business.legal_structure in (
                            "Sole proprietorship",
                            "Partnership",
                            "Limited liability company",
                            "Other",
                        )
                        else 0
                    ),
                )
                back, next_button, _ = _move_buttons(business, optional=False)
            if next_button:
                if not name.strip():
                    st.error("Business name cannot be empty.")
                else:
                    business.name = name.strip()
                    business.location = location.strip()
                    business.legal_structure = legal
                    _advance(db, business, 2)

        elif step == 2:
            choices = (
                "Retail / shop",
                "Frozen foods",
                "Food and restaurant",
                "Wholesale and distribution",
                "Fashion and clothing",
                "Pharmacy and healthcare",
                "Services",
                "Other",
            )
            with st.form("onboarding_business_type"):
                business_type = st.selectbox(
                    "Business type",
                    choices,
                    index=choices.index(business.business_type)
                    if business.business_type in choices else 0,
                )
                back, next_button, skip = _move_buttons(business)
            if back:
                _advance(db, business, 1)
            elif next_button:
                business.business_type = business_type
                _advance(db, business, 3)
            elif skip:
                _advance(db, business, 3)

        elif step == 3:
            with st.form("onboarding_opening_capital"):
                capital = st.number_input(
                    "Opening capital (₦)",
                    min_value=0.0,
                    value=business.opening_capital_kobo / 100,
                    step=1000.0,
                )
                back, next_button, skip = _move_buttons(business)
            if back:
                _advance(db, business, 2)
            elif next_button:
                business.opening_capital_kobo = to_kobo(capital)
                _advance(db, business, 4)
            elif skip:
                business.opening_capital_kobo = 0
                _advance(db, business, 4)

        elif step == 4:
            with st.form("onboarding_opening_balances"):
                cash = st.number_input(
                    "Opening cash on hand (₦)",
                    min_value=0.0,
                    value=business.opening_cash_kobo / 100,
                    step=1000.0,
                )
                bank = st.number_input(
                    "Opening bank / POS balance (₦)",
                    min_value=0.0,
                    value=business.opening_bank_kobo / 100,
                    step=1000.0,
                )
                back, next_button, skip = _move_buttons(business)
            if back:
                _advance(db, business, 3)
            elif next_button:
                cash_kobo, bank_kobo = to_kobo(cash), to_kobo(bank)
                capital_kobo = business.opening_capital_kobo
                if cash_kobo + bank_kobo != capital_kobo:
                    st.error(
                        "Opening cash plus bank balance must equal opening capital. "
                        "Change the amounts or skip this optional step."
                    )
                else:
                    business.opening_cash_kobo = cash_kobo
                    business.opening_bank_kobo = bank_kobo
                    if cash_kobo or bank_kobo:
                        transaction = Transaction(
                            business_id=business.id,
                            reference=new_reference("OPEN"),
                            transaction_type="opening_balance",
                            transaction_date=lagos_now(),
                            payment_method="cash",
                            total_kobo=capital_kobo,
                            memo="Opening cash and bank balances",
                            status="posted",
                        )
                        db.add(transaction)
                        db.flush()
                        post_journal(
                            db,
                            business.id,
                            transaction,
                            transaction.memo,
                            [
                                ("1010", cash_kobo, 0),
                                ("1020", bank_kobo, 0),
                                ("3000", 0, capital_kobo),
                            ],
                        )
                    _advance(db, business, 5)
            elif skip:
                business.opening_capital_kobo = 0
                business.opening_cash_kobo = 0
                business.opening_bank_kobo = 0
                _advance(db, business, 5)

        elif step == 5:
            with st.form("onboarding_add_product"):
                st.write("Add products now or skip this step and add them later.")
                sku = st.text_input("Product code / SKU")
                product_name = st.text_input("Product name")
                unit = st.text_input("Unit", value="pieces")
                selling_price = st.number_input(
                    "Selling price per unit (₦)", min_value=0.0, step=100.0
                )
                reorder_level = st.number_input(
                    "Restock alert at", min_value=0, step=1, value=5
                )
                back, add_product, skip = _move_buttons(
                    business, continue_label="Add product"
                )
            if back:
                _advance(db, business, 4)
            elif add_product:
                if not sku.strip() or not product_name.strip() or not unit.strip():
                    st.error("Enter a product code, name and unit.")
                elif db.scalar(
                    select(Product.id).where(
                        Product.business_id == business.id,
                        Product.sku == sku.strip(),
                    )
                ):
                    st.error("That product code already exists.")
                else:
                    category = db.scalar(
                        select(ProductCategory).where(
                            ProductCategory.business_id == business.id,
                            ProductCategory.name == "General",
                        )
                    )
                    if category is None:
                        category = ProductCategory(
                            business_id=business.id, name="General"
                        )
                        db.add(category)
                        db.flush()
                    db.add(
                        Product(
                            business_id=business.id,
                            category_id=category.id,
                            sku=sku.strip(),
                            name=product_name.strip(),
                            unit=unit.strip(),
                            selling_price_kobo=to_kobo(selling_price),
                            reorder_level=reorder_level,
                        )
                    )
                    db.commit()
                    st.success("Product added. Add another or continue.")
            elif skip:
                _advance(db, business, 6)
            products = list(
                db.scalars(
                    select(Product).where(Product.business_id == business.id)
                    .order_by(Product.name)
                )
            )
            if products and st.button("Continue to opening stock", type="primary"):
                _advance(db, business, 6)

        elif step == 6:
            products = list(
                db.scalars(
                    select(Product).where(Product.business_id == business.id)
                    .order_by(Product.name)
                )
            )
            if not products:
                st.info("Add products first, or skip this optional step.")
                if st.button("Skip opening stock"):
                    _advance(db, business, 7)
            else:
                opening_stock_key = action_idempotency_key("opening_stock")
                with st.form("onboarding_opening_stock"):
                    product = st.selectbox(
                        "Product",
                        products,
                        format_func=lambda item: f"{item.name} · {item.sku}",
                    )
                    fractional = product.unit.lower() in WEIGHT_UNITS
                    quantity = st.number_input(
                        "Quantity on hand",
                        min_value=0.001 if fractional else 1,
                        value=1.0 if fractional else 1,
                        step=0.001 if fractional else 1,
                        format="%.3f" if fractional else "%d",
                    )
                    unit_cost = st.number_input(
                        "Cost per unit (₦)", min_value=0.01, step=100.0
                    )
                    back, add_stock, skip = _move_buttons(
                        business, continue_label="Record opening stock"
                    )
                if back:
                    _advance(db, business, 5)
                elif add_stock:
                    try:
                        record_purchase(
                            db,
                            business.id,
                            [(product, quantity, to_kobo(unit_cost))],
                            "Opening stock",
                            payment_method="credit",
                            idempotency_key=opening_stock_key,
                        )
                        db.commit()
                        rotate_action_idempotency_key("opening_stock")
                        st.success("Opening stock recorded. Add more or continue.")
                    except ValueError as exc:
                        db.rollback()
                        rotate_action_idempotency_key("opening_stock")
                        st.error(str(exc))
                elif skip:
                    _advance(db, business, 7)
                stocked = any(
                    stock_quantity(db, item.id) > 0 for item in products
                )
                if stocked and st.button("Continue to suppliers", type="primary"):
                    _advance(db, business, 7)

        elif step == 7:
            with st.form("onboarding_add_supplier"):
                supplier_name = st.text_input("Supplier name")
                supplier_phone = st.text_input("Supplier phone (optional)")
                back, add_supplier, skip = _move_buttons(
                    business, continue_label="Add supplier"
                )
            if back:
                _advance(db, business, 6)
            elif add_supplier:
                if not supplier_name.strip():
                    st.error("Enter a supplier name.")
                elif db.scalar(
                    select(Supplier.id).where(
                        Supplier.business_id == business.id,
                        Supplier.name == supplier_name.strip(),
                    )
                ):
                    st.error("That supplier is already on your list.")
                else:
                    db.add(
                        Supplier(
                            business_id=business.id,
                            name=supplier_name.strip(),
                            phone=supplier_phone.strip(),
                        )
                    )
                    db.commit()
                    st.success("Supplier added. Add another or continue.")
            elif skip:
                _advance(db, business, 8)
            if st.button("Continue to finish", type="primary"):
                _advance(db, business, 8)

        else:
            st.success("Your business details are ready.")
            if st.button("Complete setup and open dashboard", type="primary"):
                _complete_setup(db, business)

        if step < 8 and st.button("Finish setup later"):
            _complete_setup(db, business)
    finally:
        db.close()
