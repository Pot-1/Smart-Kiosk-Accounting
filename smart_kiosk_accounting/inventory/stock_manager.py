from sqlalchemy import func, select

from database.models import InventoryMovement, Product, StockBatch


VALID_METHODS = {"FIFO", "LIFO", "WEIGHTED_AVERAGE"}


def stock_quantity(session, product_id: int) -> int:
    return int(
        session.scalar(
            select(func.coalesce(func.sum(StockBatch.quantity_remaining), 0)).where(
                StockBatch.product_id == product_id
            )
        )
        or 0
    )


def stock_value(session, business_id: int, product_id: int | None = None) -> int:
    from sqlalchemy import func

    statement = select(func.coalesce(func.sum(StockBatch.remaining_cost_kobo), 0)).where(
        StockBatch.business_id == business_id, StockBatch.quantity_remaining > 0
    )
    if product_id is not None:
        statement = statement.where(StockBatch.product_id == product_id)
    return int(session.scalar(statement) or 0)


def receive_stock(
    session,
    business_id: int,
    product: Product,
    quantity: int,
    total_cost_kobo: int,
    transaction_id: int | None = None,
    reason: str = "Purchase",
    movement_type: str = "purchase",
):
    if quantity <= 0 or total_cost_kobo < 0:
        raise ValueError("Stock receipt needs a positive quantity and non-negative cost.")
    batch = StockBatch(
        business_id=business_id,
        product_id=product.id,
        source_transaction_id=transaction_id,
        quantity_received=quantity,
        quantity_remaining=quantity,
        unit_cost_kobo=(total_cost_kobo + quantity // 2) // quantity,
        total_cost_kobo=total_cost_kobo,
        remaining_cost_kobo=total_cost_kobo,
    )
    session.add(batch)
    session.flush()
    session.add(
        InventoryMovement(
            business_id=business_id,
            transaction_id=transaction_id,
            product_id=product.id,
            movement_type=movement_type,
            quantity=quantity,
            unit_cost_kobo=batch.unit_cost_kobo,
            total_kobo=total_cost_kobo,
            reason=reason,
        )
    )
    return batch


def consume_stock(session, business_id: int, product_id: int, quantity: int, method="FIFO"):
    method = method.upper().replace(" ", "_")
    if method not in VALID_METHODS:
        raise ValueError("Inventory costing must be FIFO, LIFO or WEIGHTED_AVERAGE.")
    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")
    batches = list(
        session.scalars(
            select(StockBatch)
            .where(
                StockBatch.business_id == business_id,
                StockBatch.product_id == product_id,
                StockBatch.quantity_remaining > 0,
            )
            .order_by(
                StockBatch.received_at.desc() if method == "LIFO"
                else StockBatch.received_at.asc(),
                StockBatch.id.desc() if method == "LIFO" else StockBatch.id.asc(),
            )
        )
    )
    available = sum(batch.quantity_remaining for batch in batches)
    if quantity > available:
        raise ValueError(
            f"Not enough stock. Available: {available}; requested: {quantity}."
        )
    if method == "WEIGHTED_AVERAGE":
        total_cost = sum(batch.remaining_cost_kobo for batch in batches)
        cost_total = (total_cost * quantity + available // 2) // available
        remaining_quantity = quantity
        for batch in batches:
            take = min(remaining_quantity, batch.quantity_remaining)
            batch.quantity_remaining -= take
            remaining_quantity -= take
            if remaining_quantity == 0:
                break
        remaining_value = total_cost - cost_total
        remaining_quantity = available - quantity
        value_left = remaining_value
        quantity_left = remaining_quantity
        for batch in batches:
            if batch.quantity_remaining == 0:
                batch.remaining_cost_kobo = 0
                continue
            if batch.quantity_remaining == quantity_left:
                allocation = value_left
            else:
                allocation = (
                    value_left * batch.quantity_remaining + quantity_left // 2
                ) // quantity_left
            batch.remaining_cost_kobo = allocation
            value_left -= allocation
            quantity_left -= batch.quantity_remaining
        return cost_total
    remaining = quantity
    cost_total = 0
    while remaining:
        batch = batches[0]
        take = min(remaining, batch.quantity_remaining)
        if take == batch.quantity_remaining:
            take_cost = batch.remaining_cost_kobo
        else:
            take_cost = (
                batch.remaining_cost_kobo * take + batch.quantity_remaining // 2
            ) // batch.quantity_remaining
        batch.quantity_remaining -= take
        batch.remaining_cost_kobo -= take_cost
        remaining -= take
        cost_total += take_cost
        if batch.quantity_remaining == 0:
            batches.pop(0)
    return cost_total


def consume_purchase_batch(
    session, business_id: int, product_id: int, quantity: int, purchase_transaction_id: int
):
    batches = list(
        session.scalars(
            select(StockBatch)
            .where(
                StockBatch.business_id == business_id,
                StockBatch.product_id == product_id,
                StockBatch.source_transaction_id == purchase_transaction_id,
                StockBatch.quantity_remaining > 0,
            )
            .order_by(StockBatch.id)
        )
    )
    available = sum(batch.quantity_remaining for batch in batches)
    if quantity <= 0 or quantity > available:
        raise ValueError(
            f"Only {available} unit(s) from that purchase remain in stock; "
            f"cannot return {quantity}."
        )
    remaining = quantity
    cost_total = 0
    for batch in batches:
        take = min(remaining, batch.quantity_remaining)
        if take == batch.quantity_remaining:
            cost = batch.remaining_cost_kobo
        else:
            cost = (
                batch.remaining_cost_kobo * take + batch.quantity_remaining // 2
            ) // batch.quantity_remaining
        batch.quantity_remaining -= take
        batch.remaining_cost_kobo -= cost
        remaining -= take
        cost_total += cost
        if remaining == 0:
            break
    return cost_total
