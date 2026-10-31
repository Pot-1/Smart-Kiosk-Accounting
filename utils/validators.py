from decimal import Decimal, InvalidOperation


def positive_quantity(value) -> int:
    try:
        quantity = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Quantity must be a whole number.") from exc
    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")
    return quantity


def positive_amount(value) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Enter a valid amount.") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Amount must be greater than zero.")
    return amount
