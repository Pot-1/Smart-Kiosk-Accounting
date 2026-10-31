from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


CENT = Decimal("0.01")


def to_kobo(value) -> int:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise InvalidOperation
        amount = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Enter a valid amount.") from exc
    return int(amount * 100)


def from_kobo(value: int) -> Decimal:
    return (Decimal(value) / 100).quantize(CENT)


def format_naira(value, *, kobo: bool = False) -> str:
    amount = from_kobo(value) if kobo else Decimal(str(value)).quantize(CENT)
    return f"₦{amount:,.2f}"
