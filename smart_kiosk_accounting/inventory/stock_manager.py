from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import case, func, select, update

from database.models import Account, InventoryMovement, JournalLine, Product, StockBatch


VALID_METHODS = {"FIFO", "WEIGHTED_AVERAGE"}
QUANTITY_STEP = Decimal("0.001")
WEIGHT_UNITS = {"kg", "kilogram", "kilograms", "g", "gram", "grams"}


def normalize_quantity(product: Product, value, *, allow_zero=False) -> Decimal:
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Enter a valid stock quantity.") from exc
    if not quantity.is_finite() or quantity < 0 or (quantity == 0 and not allow_zero):
        raise ValueError(
            "Quantity must be zero or greater."
            if allow_zero
            else "Quantity must be greater than zero."
        )
    unit = product.unit.strip().lower()
    if unit not in WEIGHT_UNITS and quantity != quantity.to_integral_value():
        raise ValueError(f"{product.unit.title()} quantities must be whole numbers.")
    if quantity.quantize(QUANTITY_STEP) != quantity:
        raise ValueError("Stock quantities support up to three decimal places.")
    return quantity


def _round_amount(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def stock_quantity(session, product_id: int) -> Decimal:
    return Decimal(
        session.scalar(
            select(func.coalesce(func.sum(StockBatch.quantity_remaining), 0)).where(
                StockBatch.product_id == product_id
            )
        )
        or 0
    )


def stock_value(session, business_id: int, product_id: int | None = None) -> int:
    statement = select(func.coalesce(func.sum(StockBatch.remaining_cost_kobo), 0)).where(
        StockBatch.business_id == business_id, StockBatch.remaining_cost_kobo > 0
    )
    if product_id is not None:
        statement = statement.where(StockBatch.product_id == product_id)
    return int(session.scalar(statement) or 0)


def stock_totals_by_product(
    session, business_id: int
) -> dict[int, tuple[Decimal, int]]:
    return {
        product_id: (Decimal(quantity or 0), int(value or 0))
        for product_id, quantity, value in session.execute(
            select(
                StockBatch.product_id,
                func.coalesce(func.sum(StockBatch.quantity_remaining), 0),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                StockBatch.remaining_cost_kobo > 0,
                                StockBatch.remaining_cost_kobo,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
            )
            .where(StockBatch.business_id == business_id)
            .group_by(StockBatch.product_id)
        )
    }


def inventory_reconciliation(session, business_id: int) -> dict:
    products = list(
        session.scalars(
            select(Product).where(Product.business_id == business_id).order_by(Product.id)
        )
    )
    movement_totals = {
        product_id: (Decimal(quantity or 0), int(value or 0))
        for product_id, quantity, value in session.execute(
            select(
                InventoryMovement.product_id,
                func.coalesce(func.sum(InventoryMovement.quantity), 0),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                InventoryMovement.quantity >= 0,
                                InventoryMovement.total_kobo,
                            ),
                            else_=-InventoryMovement.total_kobo,
                        )
                    ),
                    0,
                ),
            )
            .where(InventoryMovement.business_id == business_id)
            .group_by(InventoryMovement.product_id)
        )
    }
    product_rows = []
    stock_totals = stock_totals_by_product(session, business_id)
    for product in products:
        recorded_quantity, recorded_value = movement_totals.get(
            product.id, (Decimal(0), 0)
        )
        current_quantity, current_value = stock_totals.get(
            product.id, (Decimal(0), 0)
        )
        product_rows.append(
            {
                "product_id": product.id,
                "product": product.name,
                "movement_quantity": recorded_quantity,
                "batch_quantity": current_quantity,
                "movement_value_kobo": recorded_value,
                "batch_value_kobo": current_value,
                "quantity_matches": recorded_quantity == current_quantity,
                "value_matches": recorded_value == current_value,
            }
        )
    stock_total = sum(row["batch_value_kobo"] for row in product_rows)
    inventory_account = session.scalar(
        select(Account).where(
            Account.business_id == business_id,
            Account.code == "1200",
        )
    )
    if inventory_account is None:
        ledger_total = None
    else:
        debit, credit = session.execute(
            select(
                func.coalesce(func.sum(JournalLine.debit_kobo), 0),
                func.coalesce(func.sum(JournalLine.credit_kobo), 0),
            ).where(
                JournalLine.business_id == business_id,
                JournalLine.account_id == inventory_account.id,
            )
        ).one()
        ledger_total = int(debit - credit)
    return {
        "products": product_rows,
        "movement_quantities_match": all(
            row["quantity_matches"] for row in product_rows
        ),
        "movement_values_match": all(row["value_matches"] for row in product_rows),
        "inventory_value_kobo": stock_total,
        "inventory_ledger_value_kobo": ledger_total,
        "inventory_ledger_matches": ledger_total == stock_total,
    }


def receive_stock(
    session,
    business_id: int,
    product: Product,
    quantity,
    total_cost_kobo: int,
    transaction_id: int | None = None,
    reason: str = "Purchase",
    movement_type: str = "purchase",
    expires_on: date | None = None,
    source_item_id: int | None = None,
):
    quantity = normalize_quantity(product, quantity)
    if product.business_id != business_id:
        raise ValueError("This product belongs to a different business.")
    if total_cost_kobo < 0:
        raise ValueError("Stock receipt cost cannot be negative.")
    if expires_on is not None and not isinstance(expires_on, date):
        raise ValueError("Enter a valid expiry date.")
    unit_cost = _round_amount(Decimal(total_cost_kobo) / quantity)
    batch = StockBatch(
        business_id=business_id,
        product_id=product.id,
        source_transaction_id=transaction_id,
        source_item_id=source_item_id,
        quantity_received=quantity,
        quantity_remaining=quantity,
        valuation_quantity_remaining=quantity,
        unit_cost_kobo=unit_cost,
        total_cost_kobo=total_cost_kobo,
        remaining_cost_kobo=total_cost_kobo,
        expires_on=expires_on,
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


def _fifo_order():
    return (StockBatch.received_at.asc(), StockBatch.id.asc())


def _fefo_order():
    return (
        case((StockBatch.expires_on.is_(None), 1), else_=0),
        StockBatch.expires_on.asc(),
        StockBatch.received_at.asc(),
        StockBatch.id.asc(),
    )


def _write_batch_changes(session, business_id, product_id, changes):
    for batch, quantity, valuation_quantity, remaining_cost in changes:
        result = session.execute(
            update(StockBatch)
            .where(
                StockBatch.id == batch.id,
                StockBatch.business_id == business_id,
                StockBatch.product_id == product_id,
                StockBatch.quantity_remaining == batch.quantity_remaining,
                StockBatch.valuation_quantity_remaining
                == batch.valuation_quantity_remaining,
                StockBatch.remaining_cost_kobo == batch.remaining_cost_kobo,
            )
            .values(
                quantity_remaining=quantity,
                valuation_quantity_remaining=valuation_quantity,
                remaining_cost_kobo=remaining_cost,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ValueError("Stock changed during this operation. Review and retry.")
        batch.quantity_remaining = quantity
        batch.valuation_quantity_remaining = valuation_quantity
        batch.remaining_cost_kobo = remaining_cost


def consume_stock(session, business_id: int, product_id: int, quantity, method="FIFO"):
    method = method.upper().replace(" ", "_")
    if method == "LIFO":
        raise ValueError("LIFO costing is unsupported; choose FIFO or weighted average.")
    if method not in VALID_METHODS:
        raise ValueError("Inventory costing must be FIFO or WEIGHTED_AVERAGE.")
    product = session.get(Product, product_id)
    if product is None or product.business_id != business_id:
        raise ValueError("The product does not belong to this business.")
    quantity = normalize_quantity(product, quantity)
    physical_batches = list(
        session.scalars(
            select(StockBatch)
            .where(
                StockBatch.business_id == business_id,
                StockBatch.product_id == product_id,
                StockBatch.quantity_remaining > 0,
            )
            .order_by(*_fefo_order())
            .with_for_update()
        )
    )
    available = sum(
        (Decimal(batch.quantity_remaining) for batch in physical_batches), Decimal(0)
    )
    if quantity > available:
        raise ValueError(
            f"Not enough stock. Available: {available}; requested: {quantity}."
        )

    remaining = quantity
    physical = []
    for batch in physical_batches:
        take = min(remaining, Decimal(batch.quantity_remaining))
        physical.append((batch, take))
        remaining -= take
        if remaining == 0:
            break

    valuation_batches = list(
        session.scalars(
            select(StockBatch)
            .where(
                StockBatch.business_id == business_id,
                StockBatch.product_id == product_id,
                StockBatch.valuation_quantity_remaining > 0,
            )
            .order_by(*_fifo_order())
            .with_for_update()
        )
    )
    valuation_quantity = sum(
        (Decimal(batch.valuation_quantity_remaining) for batch in valuation_batches),
        Decimal(0),
    )
    if valuation_quantity != available:
        raise ValueError("Inventory valuation quantity is out of sync with physical stock.")
    total_cost = sum(batch.remaining_cost_kobo for batch in valuation_batches)
    if method == "WEIGHTED_AVERAGE":
        cost_total = _round_amount(Decimal(total_cost) * quantity / available)
    else:
        cost_total = 0

    physical_taken = {batch.id: take for batch, take in physical}
    valuation_taken = {}
    cost_removed = {}
    qty_left_to_value = quantity
    for batch in valuation_batches:
        old_valuation_quantity = Decimal(batch.valuation_quantity_remaining)
        take = min(qty_left_to_value, old_valuation_quantity)
        if take == 0:
            break
        if method == "FIFO":
            if take == old_valuation_quantity:
                cost = batch.remaining_cost_kobo
            else:
                cost = _round_amount(
                    Decimal(batch.remaining_cost_kobo) * take / old_valuation_quantity
                )
        else:
            cost = 0
        valuation_taken[batch.id] = take
        cost_removed[batch.id] = cost
        qty_left_to_value -= take

    if method == "FIFO":
        cost_total = sum(cost_removed.values())

    if method == "WEIGHTED_AVERAGE":
        remaining_value = total_cost - cost_total
        remaining_valuation_quantity = valuation_quantity - quantity
        value_left = remaining_value
        quantity_left = remaining_valuation_quantity
        for batch in valuation_batches:
            new_valuation_quantity = (
                Decimal(batch.valuation_quantity_remaining)
                - valuation_taken.get(batch.id, Decimal(0))
            )
            if new_valuation_quantity == 0:
                new_value = 0
            elif new_valuation_quantity == quantity_left:
                new_value = value_left
            else:
                new_value = _round_amount(
                    Decimal(value_left) * new_valuation_quantity / quantity_left
                )
            cost_removed[batch.id] = batch.remaining_cost_kobo - new_value
            value_left -= new_value
            quantity_left -= new_valuation_quantity

    changes = []
    by_id = {batch.id: batch for batch in physical_batches}
    by_id.update({batch.id: batch for batch in valuation_batches})
    for batch in by_id.values():
        new_quantity = Decimal(batch.quantity_remaining) - physical_taken.get(
            batch.id, Decimal(0)
        )
        new_valuation_quantity = Decimal(batch.valuation_quantity_remaining) - (
            valuation_taken.get(batch.id, Decimal(0))
        )
        new_cost = batch.remaining_cost_kobo - cost_removed.get(batch.id, 0)
        changes.append(
            (batch, new_quantity, new_valuation_quantity, new_cost)
        )
    _write_batch_changes(session, business_id, product_id, changes)
    return cost_total


def consume_purchase_batch(
    session,
    business_id: int,
    product_id: int,
    quantity,
    purchase_transaction_id: int,
    method="FIFO",
    purchase_item_id: int | None = None,
):
    method = method.upper().replace(" ", "_")
    if method not in VALID_METHODS:
        raise ValueError("Inventory costing must be FIFO or WEIGHTED_AVERAGE.")
    product = session.get(Product, product_id)
    if product is None or product.business_id != business_id:
        raise ValueError("The product does not belong to this business.")
    quantity = normalize_quantity(product, quantity)
    statement = select(StockBatch).where(
        StockBatch.business_id == business_id,
        StockBatch.product_id == product_id,
        StockBatch.source_transaction_id == purchase_transaction_id,
        StockBatch.quantity_remaining > 0,
    )
    if purchase_item_id is not None:
        statement = statement.where(StockBatch.source_item_id == purchase_item_id)
    batches = list(
        session.scalars(statement.order_by(StockBatch.id).with_for_update())
    )
    if not batches and purchase_item_id is not None:
        batches = list(
            session.scalars(
                select(StockBatch)
                .where(
                    StockBatch.business_id == business_id,
                    StockBatch.product_id == product_id,
                    StockBatch.source_transaction_id == purchase_transaction_id,
                    StockBatch.source_item_id.is_(None),
                    StockBatch.quantity_remaining > 0,
                )
                .order_by(StockBatch.id)
                .with_for_update()
            )
        )
    available = sum((Decimal(batch.quantity_remaining) for batch in batches), Decimal(0))
    if quantity > available:
        raise ValueError(
            f"Only {available} unit(s) from that purchase remain in stock; "
            f"cannot return {quantity}."
        )
    physical_taken = {}
    remaining = quantity
    for batch in batches:
        old_quantity = Decimal(batch.quantity_remaining)
        take = min(remaining, old_quantity)
        physical_taken[batch.id] = take
        remaining -= take
        if remaining == 0:
            break

    valuation_batches = list(
        session.scalars(
            select(StockBatch)
            .where(
                StockBatch.business_id == business_id,
                StockBatch.product_id == product_id,
                StockBatch.valuation_quantity_remaining > 0,
            )
            .order_by(*_fifo_order())
            .with_for_update()
        )
    )
    if method == "FIFO" and purchase_item_id is not None:
        valuation_batches.sort(
            key=lambda batch: (
                batch.source_item_id != purchase_item_id,
                batch.received_at,
                batch.id,
            )
        )
    valuation_quantity = sum(
        (Decimal(batch.valuation_quantity_remaining) for batch in valuation_batches),
        Decimal(0),
    )
    if valuation_quantity != stock_quantity(session, product_id):
        raise ValueError("Inventory valuation quantity is out of sync with physical stock.")
    total_cost = sum(batch.remaining_cost_kobo for batch in valuation_batches)
    if method == "WEIGHTED_AVERAGE":
        cost_total = _round_amount(Decimal(total_cost) * quantity / valuation_quantity)
    else:
        cost_total = 0
    valuation_taken = {}
    cost_removed = {}
    remaining_to_value = quantity
    for batch in valuation_batches:
        old_quantity = Decimal(batch.valuation_quantity_remaining)
        take = min(remaining_to_value, old_quantity)
        if take == 0:
            break
        if method == "FIFO":
            cost = (
                batch.remaining_cost_kobo
                if take == old_quantity
                else _round_amount(Decimal(batch.remaining_cost_kobo) * take / old_quantity)
            )
            cost_removed[batch.id] = cost
        valuation_taken[batch.id] = take
        remaining_to_value -= take
    if method == "FIFO":
        cost_total = sum(cost_removed.values())
    else:
        value_left = total_cost - cost_total
        quantity_left = valuation_quantity - quantity
        for batch in valuation_batches:
            new_valuation_quantity = Decimal(batch.valuation_quantity_remaining) - (
                valuation_taken.get(batch.id, Decimal(0))
            )
            new_value = (
                0
                if new_valuation_quantity == 0
                else value_left
                if new_valuation_quantity == quantity_left
                else _round_amount(Decimal(value_left) * new_valuation_quantity / quantity_left)
            )
            cost_removed[batch.id] = batch.remaining_cost_kobo - new_value
            value_left -= new_value
            quantity_left -= new_valuation_quantity

    by_id = {batch.id: batch for batch in batches}
    by_id.update({batch.id: batch for batch in valuation_batches})
    changes = []
    for batch in by_id.values():
        new_quantity = Decimal(batch.quantity_remaining) - physical_taken.get(
            batch.id, Decimal(0)
        )
        new_valuation_quantity = Decimal(
            batch.valuation_quantity_remaining
        ) - valuation_taken.get(batch.id, Decimal(0))
        new_cost = batch.remaining_cost_kobo - cost_removed.get(batch.id, 0)
        changes.append((batch, new_quantity, new_valuation_quantity, new_cost))
    _write_batch_changes(session, business_id, product_id, changes)
    return cost_total
