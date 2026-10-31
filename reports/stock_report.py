from sqlalchemy import select

from database.models import Product
from inventory.stock_manager import stock_quantity, stock_value


def stock_rows(session, business_id: int):
    rows = []
    for product in session.scalars(
        select(Product).where(
            Product.business_id == business_id, Product.active.is_(True)
        ).order_by(Product.name)
    ):
        rows.append({
            "SKU": product.sku,
            "Product": product.name,
            "On hand": stock_quantity(session, product.id),
            "Reorder at": product.reorder_level,
            "Stock value (kobo)": stock_value(session, business_id, product.id),
        })
    return rows
