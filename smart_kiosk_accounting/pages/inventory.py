import pandas as pd
import streamlit as st
from sqlalchemy import select

from database.models import Product
from pages.common import current_business_id, product_stock_frame, session
from utils.currency import to_kobo


def _import_product_file(db, upload, business_id):
    frame = (
        pd.read_csv(upload) if upload.name.lower().endswith(".csv")
        else pd.read_excel(upload)
    )
    normalized = {
        "".join(character for character in str(column).lower() if character.isalnum()): column
        for column in frame.columns
    }
    required = ("sku", "product", "unit", "sellingprice", "reorderlevel")
    if any(column not in normalized for column in required):
        raise ValueError(
            "File must include SKU, Product, Unit, Selling Price, and Reorder Level columns."
        )
    prepared = []
    seen = set()
    for _, row in frame.iterrows():
        sku = str(row[normalized["sku"]]).strip()
        name = str(row[normalized["product"]]).strip()
        if not sku or not name or sku.lower() == "nan" or name.lower() == "nan":
            raise ValueError("Every product row must have a SKU and name.")
        if sku in seen:
            raise ValueError(f"Duplicate SKU in file: {sku}")
        seen.add(sku)
        prepared.append(Product(
            business_id=business_id,
            sku=sku,
            name=name,
            unit=str(row[normalized["unit"]]).strip(),
            selling_price_kobo=to_kobo(row[normalized["sellingprice"]]),
            reorder_level=max(0, int(row[normalized["reorderlevel"]])),
        ))
    existing = set(db.scalars(
        select(Product.sku).where(Product.business_id == business_id)
    ))
    duplicates = seen & existing
    if duplicates:
        raise ValueError("These SKUs already exist: " + ", ".join(sorted(duplicates)))
    with db.begin_nested():
        db.add_all(prepared)
        db.flush()
    return len(prepared)


def render():
    st.title("Inventory")
    st.caption("Keep track of what is in your shop, what it cost, and what needs restocking.")
    db = session()
    business_id = current_business_id()
    try:
        with st.expander("Add a product", expanded=not bool(product_stock_frame(db).shape[0])):
            with st.form("new_product"):
                name = st.text_input("Product name")
                sku = st.text_input("SKU / product code")
                category_name = st.text_input("Category", value="General")
                unit = st.selectbox(
                    "Sold by",
                    ["pieces", "cartons", "packs", "kg", "grams", "bags"],
                )
                price = st.number_input("Selling price (₦)", min_value=0.0, step=100.0)
                reorder = st.number_input("Remind me when stock reaches", min_value=0, value=5)
                submitted = st.form_submit_button("Add product", type="primary")
            if submitted:
                try:
                    if not name.strip() or not sku.strip() or not category_name.strip():
                        raise ValueError("Enter a product name, SKU, and category.")
                    duplicate = db.scalar(
                        select(Product).where(
                            Product.business_id == business_id, Product.sku == sku.strip()
                        )
                    )
                    if duplicate:
                        raise ValueError("That SKU is already in use.")
                    category = db.scalar(
                        select(ProductCategory).where(
                            ProductCategory.business_id == business_id,
                            ProductCategory.name == category_name.strip(),
                        )
                    )
                    if category is None:
                        category = ProductCategory(
                            business_id=business_id, name=category_name.strip()
                        )
                        db.add(category)
                        db.flush()
                    db.add(Product(
                        business_id=business_id, category_id=category.id,
                        name=name.strip(), sku=sku.strip(),
                        unit=unit, selling_price_kobo=to_kobo(price),
                        reorder_level=int(reorder),
                    ))
                    db.commit()
                    st.success(f"{name} added.")
                    st.rerun()
                except ValueError as exc:
                    db.rollback()
                    st.error(str(exc))
        with st.expander("Import products from CSV / Excel"):
            upload = st.file_uploader(
                "Choose a CSV or Excel file", type=["csv", "xlsx", "xls"],
                key="product_import",
            )
            st.caption("Required columns: SKU, Product, Unit, Selling Price, Reorder Level.")
            if upload is not None and st.button("Import products"):
                try:
                    count = _import_product_file(db, upload, business_id)
                    db.commit()
                    st.success(f"Imported {count} product(s).")
                    st.rerun()
                except (ValueError, TypeError, KeyError) as exc:
                    db.rollback()
                    st.error(f"Could not import products: {exc}")
        stock = product_stock_frame(db)
        if stock.empty:
            st.info("No products yet. Add your first product above.")
        else:
            st.dataframe(stock, width="stretch", hide_index=True)
            st.caption("Stock value is based on the cost of the remaining purchase batches.")
    finally:
        db.close()
