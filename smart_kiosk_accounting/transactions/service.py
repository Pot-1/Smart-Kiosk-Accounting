import hashlib
import json
from functools import wraps
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from database.models import (
    AuditLog,
    Business,
    Customer,
    Expense,
    InventoryMovement,
    Payment,
    Product,
    ReceiptAttachment,
    ReturnRecord,
    StockAdjustment,
    StockCount,
    StockCountLine,
    Supplier,
    Transaction,
    TransactionItem,
)
from database.tenant import require_business_access
from inventory.stock_manager import (
    consume_purchase_batch,
    consume_stock,
    normalize_quantity,
    receive_stock,
    stock_quantity,
)
from accounting.journal_engine import post_journal
from utils.currency import format_naira
from utils.helpers import lagos_now, new_reference


def _fingerprint_value(value):
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, (list, tuple)):
        return [_fingerprint_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _fingerprint_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if hasattr(value, "business_id") and hasattr(value, "id"):
        return {
            "model": type(value).__name__,
            "id": value.id,
            "business_id": value.business_id,
        }
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _request_fingerprint(function, business_id, args, kwargs):
    payload = {
        "operation": function.__name__,
        "business_id": business_id,
        "args": _fingerprint_value(args),
        "kwargs": _fingerprint_value(kwargs),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _atomic(function):
    @wraps(function)
    def wrapped(session, *args, **kwargs):
        idempotency_key = kwargs.pop("idempotency_key", None)
        business_id = kwargs.get("business_id")
        if business_id is None and args:
            business_id = args[0]
        permission = {
            "record_purchase": "purchases",
            "record_sale": "sales",
            "record_expense": "expenses",
            "record_owner_funds": "business_settings",
            "record_customer_payment": "customers",
            "record_supplier_payment": "suppliers",
            "record_stock_adjustment": "stock_losses",
            "record_stock_count": "stock_losses",
            "reverse_transaction": "journals",
            "record_sales_return": "sales",
            "record_purchase_return": "purchases",
        }.get(function.__name__)
        if business_id is None:
            raise PermissionError("A business context is required.")
        require_business_access(session, business_id, permission)
        fingerprint = None
        idempotency_model = (
            StockCount if function.__name__ == "record_stock_count" else Transaction
        )
        if idempotency_key is not None:
            idempotency_key = str(idempotency_key).strip()
            if not idempotency_key or len(idempotency_key) > 100:
                raise ValueError("Idempotency keys must contain 1 to 100 characters.")
            fingerprint = _request_fingerprint(function, business_id, args, kwargs)
            existing = session.scalar(
                select(idempotency_model).where(
                    idempotency_model.business_id == business_id,
                    idempotency_model.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise ValueError(
                        "This idempotency key was already used for a different request."
                    )
                if (
                    isinstance(existing, Transaction)
                    and existing.status != "posted"
                ):
                    raise ValueError("This idempotency key belongs to a reversed transaction.")
                return existing

        old_key = session.info.get("transaction_idempotency_key")
        old_fingerprint = session.info.get("transaction_request_fingerprint")
        session.info["transaction_idempotency_key"] = idempotency_key
        session.info["transaction_request_fingerprint"] = fingerprint
        try:
            try:
                with session.begin_nested():
                    return function(session, *args, **kwargs)
            except IntegrityError:
                if idempotency_key is None:
                    raise
                existing = session.scalar(
                    select(idempotency_model).where(
                        idempotency_model.business_id == business_id,
                        idempotency_model.idempotency_key == idempotency_key,
                    )
                )
                if existing is None:
                    raise
                if existing.request_fingerprint != fingerprint:
                    raise ValueError(
                        "This idempotency key was already used for a different request."
                    )
                return existing
            except OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    raise ValueError(
                        "Stock or account data changed concurrently. Review and retry."
                    ) from exc
                raise
        finally:
            if old_key is None:
                session.info.pop("transaction_idempotency_key", None)
            else:
                session.info["transaction_idempotency_key"] = old_key
            if old_fingerprint is None:
                session.info.pop("transaction_request_fingerprint", None)
            else:
                session.info["transaction_request_fingerprint"] = old_fingerprint

    return wrapped


def _transaction(session, business_id, kind, name="", method="cash", total=0, memo=""):
    transaction = Transaction(
        business_id=business_id,
        reference=new_reference(kind[:3].upper()),
        transaction_type=kind,
        transaction_date=lagos_now(),
        counterparty_name=name,
        payment_method=method,
        total_kobo=total,
        credit_amount_kobo=total if method == "credit" else 0,
        memo=memo,
        status="posted",
        created_by_user_id=session.info.get("tenant_user_id"),
        idempotency_key=session.info.get("transaction_idempotency_key"),
        request_fingerprint=session.info.get("transaction_request_fingerprint"),
    )
    session.add(transaction)
    session.flush()
    return transaction


def _quantity_amount(quantity: Decimal, unit_price_kobo: int) -> int:
    return int(
        (quantity * Decimal(unit_price_kobo)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def _unit_cost(total_cost_kobo: int, quantity: Decimal) -> int:
    return int(
        (Decimal(total_cost_kobo) / quantity).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def _validate_split_payments(payments, total, allowed_methods):
    normalized = []
    for method, amount in payments or ():
        if method not in allowed_methods:
            raise ValueError(f"{method} is not valid for a split payment.")
        if not isinstance(amount, int) or amount <= 0:
            raise ValueError(
                "Every split payment amount must be a positive whole-kobo value."
            )
        normalized.append((method, amount))
    if not normalized or sum(amount for _, amount in normalized) != total:
        raise ValueError("Split payment amounts must exactly equal the transaction total.")
    return normalized


def _attach_receipt(session, business_id, transaction, image_data, file_name):
    if image_data:
        session.add(
            ReceiptAttachment(
                business_id=business_id,
                transaction_id=transaction.id,
                file_name=(file_name or "receipt.jpg")[:200],
                content_type="image/jpeg",
                image_data=image_data,
            )
        )


def _method_account(method: str, *, is_payment=False):
    method = method.lower()
    if method == "cash":
        return "1010"
    if method in ("bank", "pos", "transfer"):
        return "1020"
    if method == "credit" and not is_payment:
        return "1100"
    if method == "credit" and is_payment:
        return "2000"
    raise ValueError("Choose cash, bank, POS or credit.")


def _ensure_customer(session, business_id: int, name: str):
    if not name.strip():
        return None
    party = session.scalar(
        select(Customer).where(
            Customer.business_id == business_id, Customer.name == name.strip()
        )
    )
    if party is None:
        party = Customer(business_id=business_id, name=name.strip())
        session.add(party)
        session.flush()
    return party


def _ensure_supplier(session, business_id: int, name: str):
    if not name.strip():
        return None
    party = session.scalar(
        select(Supplier).where(
            Supplier.business_id == business_id, Supplier.name == name.strip()
        )
    )
    if party is None:
        party = Supplier(business_id=business_id, name=name.strip())
        session.add(party)
        session.flush()
    return party


def _party_balance(session, business_id: int, party_name: str, party_type: str):
    if party_type == "customer":
        credit_types = ("sale", "sales_return")
        payment_types = ("customer_payment",)
    else:
        credit_types = ("purchase", "purchase_return", "expense")
        payment_types = ("supplier_payment",)
    credit_sums = {
        kind: int(
            session.scalar(
                select(func.coalesce(func.sum(Transaction.credit_amount_kobo), 0)).where(
                    Transaction.business_id == business_id,
                    Transaction.counterparty_name == party_name,
                    Transaction.transaction_type == kind,
                    Transaction.status == "posted",
                )
            )
            or 0
        )
        for kind in credit_types
    }
    charged = (
        credit_sums["sale"] - credit_sums["sales_return"]
        if party_type == "customer"
        else credit_sums["purchase"]
        + credit_sums["expense"]
        - credit_sums["purchase_return"]
    )
    paid = session.scalar(
        select(func.coalesce(func.sum(Transaction.total_kobo), 0)).where(
            Transaction.business_id == business_id,
            Transaction.counterparty_name == party_name,
            Transaction.transaction_type.in_(payment_types),
            Transaction.status == "posted",
        )
    ) or 0
    if party_type == "customer":
        return int(paid - charged)
    return int(charged - paid)


@_atomic
def record_purchase(
    session,
    business_id: int,
    items,
    supplier_name: str,
    payment_method: str = "credit",
    transport_kobo: int = 0,
    receipt_image: bytes | None = None,
    receipt_name: str = "",
    split_payments=None,
):
    items = list(items)
    if not items:
        raise ValueError("Add at least one product to the purchase.")
    if not supplier_name.strip():
        raise ValueError("Enter a supplier name.")
    if transport_kobo < 0:
        raise ValueError("Transport cost cannot be negative.")
    if payment_method not in ("cash", "bank", "pos", "credit", "transfer", "split"):
        raise ValueError("Choose cash, bank, POS or credit for this purchase.")
    lines = []
    base_total = 0
    for item in items:
        if len(item) not in (3, 4):
            raise ValueError("Purchase items must include a product, quantity, and cost.")
        product, quantity, unit_cost_kobo = item[:3]
        expires_on = item[3] if len(item) == 4 else None
        if product.business_id != business_id:
            raise ValueError("A purchase item belongs to a different business.")
        quantity = normalize_quantity(product, quantity)
        if unit_cost_kobo <= 0:
            raise ValueError("Purchase quantity and unit cost must be greater than zero.")
        line_total = _quantity_amount(quantity, unit_cost_kobo)
        lines.append([product, quantity, unit_cost_kobo, line_total, 0, expires_on])
        base_total += line_total
    freight_left = transport_kobo
    for index, line in enumerate(lines):
        allocation = (
            transport_kobo * line[3] // base_total if base_total else 0
        )
        if index == len(lines) - 1:
            allocation = freight_left
        line[4] = allocation
        freight_left -= allocation
    total = base_total + transport_kobo
    transaction = _transaction(
        session, business_id, "purchase", supplier_name, payment_method, total,
        "Purchase stock" + (
            f", including transport {format_naira(transport_kobo, kobo=True)}"
            if transport_kobo else ""
        ),
    )
    if payment_method == "split":
        split_payments = _validate_split_payments(
            split_payments,
            total,
            {"cash", "bank", "pos", "transfer", "credit"},
        )
        transaction.credit_amount_kobo = sum(
            amount for method, amount in split_payments if method == "credit"
        )
    elif split_payments:
        raise ValueError("Split amounts are only accepted for split payments.")
    transaction.supplier_id = _ensure_supplier(session, business_id, supplier_name).id
    _attach_receipt(session, business_id, transaction, receipt_image, receipt_name)
    for product, quantity, unit_cost, line_total, freight, expires_on in lines:
        landed_cost = line_total + freight
        item = TransactionItem(
            business_id=business_id,
            transaction_id=transaction.id,
            product_id=product.id,
            description=product.name,
            quantity=quantity,
            unit_price_kobo=unit_cost,
            total_kobo=line_total,
            cost_kobo=landed_cost,
        )
        session.add(item)
        session.flush()
        receive_stock(
            session,
            business_id,
            product,
            quantity,
            landed_cost,
            transaction.id,
            expires_on=expires_on,
            source_item_id=item.id,
        )
    credit_lines = (
        [
            (
                "2000" if method == "credit" else _method_account(method),
                0,
                amount,
            )
            for method, amount in split_payments
        ]
        if payment_method == "split"
        else [
            (
                "2000" if payment_method == "credit" else _method_account(payment_method),
                0,
                total,
            )
        ]
    )
    post_journal(
        session, business_id, transaction, transaction.memo,
        [("1200", total, 0), *credit_lines],
    )
    return transaction


@_atomic
def record_sale(
    session, business_id: int, items, payment_method="cash", customer_name="",
    receipt_image: bytes | None = None, receipt_name: str = "",
    split_payments=None,
):
    items = list(items)
    if not items:
        raise ValueError("Add at least one product to the sale.")
    if payment_method not in ("cash", "bank", "pos", "credit", "transfer", "split"):
        raise ValueError("Choose cash, bank, POS or credit for this sale.")
    sale_lines = []
    total = cost_total = 0
    business = session.get(Business, business_id)
    if business is None:
        raise ValueError("Business not found.")
    costing_method = business.inventory_method or "FIFO"
    for product, quantity, unit_price_kobo in items:
        if product.business_id != business_id:
            raise ValueError("A sale item belongs to a different business.")
        quantity = normalize_quantity(product, quantity)
        if unit_price_kobo <= 0:
            raise ValueError("Sale quantity and selling price must be greater than zero.")
        cost = consume_stock(session, business_id, product.id, quantity, costing_method)
        amount = _quantity_amount(quantity, unit_price_kobo)
        sale_lines.append((product, quantity, unit_price_kobo, amount, cost))
        total += amount
        cost_total += cost
    transaction = _transaction(
        session, business_id, "sale", customer_name, payment_method, total, "Sale"
    )
    if payment_method == "split":
        split_payments = _validate_split_payments(
            split_payments,
            total,
            {"cash", "bank", "pos", "transfer", "credit"},
        )
        credit_amount = sum(
            amount for method, amount in split_payments if method == "credit"
        )
        if credit_amount and not customer_name.strip():
            raise ValueError("Enter a customer name for the credit portion.")
        transaction.credit_amount_kobo = credit_amount
    elif split_payments:
        raise ValueError("Split amounts are only accepted for split payments.")
    if payment_method == "credit" and not customer_name.strip():
        raise ValueError("Enter a customer name for a credit sale.")
    if customer_name.strip():
        transaction.customer_id = _ensure_customer(session, business_id, customer_name).id
    _attach_receipt(session, business_id, transaction, receipt_image, receipt_name)
    for product, quantity, unit_price, amount, cost in sale_lines:
        session.add(
            TransactionItem(
                business_id=business_id,
                transaction_id=transaction.id,
                product_id=product.id,
                description=product.name,
                quantity=quantity,
                unit_price_kobo=unit_price,
                total_kobo=amount,
                cost_kobo=cost,
            )
        )
        session.add(
            InventoryMovement(
                business_id=business_id,
                transaction_id=transaction.id,
                product_id=product.id,
                movement_type="sale",
                quantity=-quantity,
                unit_cost_kobo=_unit_cost(cost, quantity),
                total_kobo=cost,
                reason="Goods sold",
            )
        )
    debit_lines = (
        [
            (_method_account(method), amount, 0)
            for method, amount in split_payments
        ]
        if payment_method == "split"
        else [(_method_account(payment_method), total, 0)]
    )
    post_journal(
        session, business_id, transaction, "Sale",
        [*debit_lines, ("4000", 0, total),
         ("5000", cost_total, 0), ("1200", 0, cost_total)],
    )
    return transaction


@_atomic
def record_expense(
    session, business_id: int, category: str, amount_kobo: int,
    payment_method="cash", description="", supplier_name="",
    receipt_image: bytes | None = None, receipt_name: str = "",
):
    if amount_kobo <= 0 or not category.strip():
        raise ValueError("Enter an expense category and positive amount.")
    if payment_method == "credit" and not supplier_name.strip():
        raise ValueError("Enter who you owe for this credit expense.")
    transaction = _transaction(
        session, business_id, "expense", supplier_name, payment_method, amount_kobo,
        description or category,
    )
    if payment_method == "credit":
        transaction.supplier_id = _ensure_supplier(session, business_id, supplier_name).id
    _attach_receipt(session, business_id, transaction, receipt_image, receipt_name)
    credit_code = "2000" if payment_method == "credit" else _method_account(payment_method)
    post_journal(
        session, business_id, transaction, transaction.memo,
        [("6000", amount_kobo, 0), (credit_code, 0, amount_kobo)],
    )
    session.add(
        Expense(
            business_id=business_id, transaction_id=transaction.id,
            category=category.strip(), amount_kobo=amount_kobo,
        )
    )
    return transaction


@_atomic
def record_owner_funds(
    session, business_id: int, amount_kobo: int, direction="capital", method="cash", note=""
):
    if amount_kobo <= 0:
        raise ValueError("Amount must be greater than zero.")
    if direction not in ("capital", "drawing"):
        raise ValueError("Choose owner capital or drawing.")
    if method not in ("cash", "bank", "pos", "transfer"):
        raise ValueError("Choose cash, bank or POS for owner funds.")
    cash_code = _method_account(method)
    transaction = _transaction(
        session, business_id, f"owner_{direction}", "Owner", method,
        amount_kobo, note or f"Owner {direction}",
    )
    if direction == "capital":
        lines = [(cash_code, amount_kobo, 0), ("3000", 0, amount_kobo)]
    else:
        lines = [("3100", amount_kobo, 0), (cash_code, 0, amount_kobo)]
    post_journal(session, business_id, transaction, transaction.memo, lines)
    return transaction


@_atomic
def record_customer_payment(
    session, business_id: int, customer_name: str, amount_kobo: int, payment_method="cash"
):
    if amount_kobo <= 0 or not customer_name.strip():
        raise ValueError("Enter a customer and a positive payment amount.")
    if payment_method not in ("cash", "bank", "pos", "transfer"):
        raise ValueError("Customer payments must be received as cash, bank transfer or POS.")
    if amount_kobo > max(0, -_party_balance(session, business_id, customer_name.strip(), "customer")):
        raise ValueError("Payment is greater than this customer's outstanding balance.")
    transaction = _transaction(
        session, business_id, "customer_payment", customer_name, payment_method,
        amount_kobo, "Customer payment received",
    )
    transaction.customer_id = _ensure_customer(session, business_id, customer_name).id
    post_journal(
        session, business_id, transaction, transaction.memo,
        [(_method_account(payment_method), amount_kobo, 0), ("1100", 0, amount_kobo)],
    )
    session.add(
        Payment(
            business_id=business_id, transaction_id=transaction.id,
            party_type="customer", party_name=customer_name.strip(),
            amount_kobo=amount_kobo, payment_method=payment_method,
        )
    )
    return transaction


@_atomic
def record_supplier_payment(
    session, business_id: int, supplier_name: str, amount_kobo: int, payment_method="cash"
):
    if amount_kobo <= 0 or not supplier_name.strip():
        raise ValueError("Enter a supplier and a positive payment amount.")
    if payment_method not in ("cash", "bank", "pos", "transfer"):
        raise ValueError("Supplier payments must be cash, bank transfer or POS.")
    if amount_kobo > max(0, _party_balance(session, business_id, supplier_name.strip(), "supplier")):
        raise ValueError("Payment is greater than the amount owed to this supplier.")
    transaction = _transaction(
        session, business_id, "supplier_payment", supplier_name, payment_method,
        amount_kobo, "Supplier payment",
    )
    transaction.supplier_id = _ensure_supplier(session, business_id, supplier_name).id
    post_journal(
        session, business_id, transaction, transaction.memo,
        [("2000", amount_kobo, 0), (_method_account(payment_method), 0, amount_kobo)],
    )
    session.add(
        Payment(
            business_id=business_id, transaction_id=transaction.id,
            party_type="supplier", party_name=supplier_name.strip(),
            amount_kobo=amount_kobo, payment_method=payment_method,
        )
    )
    return transaction


@_atomic
def record_stock_adjustment(
    session, business_id: int, product: Product, quantity: int, reason: str, notes=""
):
    if reason not in {"missing", "stolen", "spoiled", "damaged", "supplier_shortage"}:
        raise ValueError("Choose a supported stock-loss reason.")
    if product.business_id != business_id:
        raise ValueError("This product belongs to a different business.")
    quantity = normalize_quantity(product, quantity)
    business = session.get(Business, business_id)
    previous_quantity = stock_quantity(session, product.id)
    cost = consume_stock(
        session, business_id, product.id, quantity,
        business.inventory_method if business else "FIFO",
    )
    transaction = _transaction(
        session, business_id, "stock_adjustment", "", "none", cost,
        f"{reason.replace('_', ' ').title()}: {notes}".strip(),
    )
    session.add(
        InventoryMovement(
            business_id=business_id, transaction_id=transaction.id,
            product_id=product.id, movement_type=reason, quantity=-quantity,
            unit_cost_kobo=_unit_cost(cost, Decimal(quantity)), total_kobo=cost,
            reason=notes or reason.replace("_", " "),
        )
    )
    session.add(
        StockAdjustment(
            business_id=business_id, transaction_id=transaction.id,
            product_id=product.id, quantity=quantity,
            unit_cost_kobo=_unit_cost(cost, Decimal(quantity)),
            total_cost_kobo=cost,
            reason=reason, notes=notes,
        )
    )
    session.add(
        AuditLog(
            business_id=business_id,
            user_id=session.info.get("tenant_user_id"),
            transaction_id=transaction.id,
            transaction_reference=transaction.reference,
            action="stock_adjusted",
            details=f"{product.name}: {reason}; {notes}".strip(),
            previous_value=str(previous_quantity),
            new_value=str(previous_quantity - quantity),
        )
    )
    post_journal(
        session, business_id, transaction, transaction.memo,
        [("6010", cost, 0), ("1200", 0, cost)],
        allow_zero=True,
    )
    return transaction


@_atomic
def record_stock_count(session, business_id: int, counts, reason: str):
    reason = reason.strip()
    if not reason:
        raise ValueError("Enter a reason for this physical stock count.")
    counts = list(counts)
    if not counts:
        raise ValueError("Choose at least one product to count.")
    prepared = []
    seen_products = set()
    for row in counts:
        if len(row) not in (2, 3, 4):
            raise ValueError(
                "Each count needs a product, counted quantity, and adjustment details."
            )
        product, counted_quantity = row[:2]
        adjustment_reason = row[2].strip() if len(row) >= 3 else ""
        unit_cost_kobo = row[3] if len(row) == 4 else 0
        if product.business_id != business_id:
            raise ValueError("A counted product belongs to a different business.")
        if product.id in seen_products:
            raise ValueError("A product can only appear once in a stock count.")
        seen_products.add(product.id)
        counted_quantity = normalize_quantity(
            product, counted_quantity, allow_zero=True
        )
        expected = stock_quantity(session, product.id)
        variance = counted_quantity - expected
        if variance < 0 and adjustment_reason not in {
            "missing",
            "stolen",
            "spoiled",
            "damaged",
            "supplier_shortage",
        }:
            raise ValueError(
                f"Choose a loss reason for the discrepancy in {product.name}."
            )
        if variance > 0 and unit_cost_kobo <= 0:
            raise ValueError(
                f"Enter the unit cost for the additional {product.name} stock."
            )
        prepared.append(
            (product, expected, counted_quantity, variance, adjustment_reason, unit_cost_kobo)
        )

    stock_count = StockCount(
        business_id=business_id,
        status="completed",
        reason=reason,
        created_by_user_id=session.info.get("tenant_user_id"),
        idempotency_key=session.info.get("transaction_idempotency_key"),
        request_fingerprint=session.info.get("transaction_request_fingerprint"),
    )
    session.add(stock_count)
    session.flush()
    changed = [row for row in prepared if row[3] != 0]
    transaction = None
    if changed:
        transaction = _transaction(
            session,
            business_id,
            "stock_count",
            "",
            "none",
            0,
            f"Physical stock count: {reason}",
        )
        stock_count.transaction_id = transaction.id

    loss_cost = 0
    gain_cost = 0
    for product, expected, counted, variance, loss_reason, unit_cost in prepared:
        session.add(
            StockCountLine(
                business_id=business_id,
                stock_count_id=stock_count.id,
                product_id=product.id,
                expected_quantity=expected,
                counted_quantity=counted,
                variance=variance,
                adjustment_reason=loss_reason if variance < 0 else (
                    "found" if variance > 0 else ""
                ),
            )
        )
        if variance == 0:
            continue
        if variance < 0:
            quantity = -variance
            cost = consume_stock(
                session,
                business_id,
                product.id,
                quantity,
                session.get(Business, business_id).inventory_method or "FIFO",
            )
            reason_code = loss_reason
            loss_cost += cost
            session.add(
                InventoryMovement(
                    business_id=business_id,
                    transaction_id=transaction.id,
                    product_id=product.id,
                    movement_type=reason_code,
                    quantity=-quantity,
                    unit_cost_kobo=_unit_cost(cost, quantity),
                    total_kobo=cost,
                    reason=f"{reason}: {reason_code}",
                )
            )
            session.add(
                StockAdjustment(
                    business_id=business_id,
                    transaction_id=transaction.id,
                    product_id=product.id,
                    quantity=quantity,
                    unit_cost_kobo=_unit_cost(cost, quantity),
                    total_cost_kobo=cost,
                    reason=reason_code,
                    notes=f"{reason}: {reason_code}",
                )
            )
        else:
            quantity = variance
            cost = _quantity_amount(quantity, unit_cost)
            gain_cost += cost
            receive_stock(
                session,
                business_id,
                product,
                quantity,
                cost,
                transaction.id,
                f"{reason}: found stock",
                movement_type="found",
            )
        session.add(
            AuditLog(
                business_id=business_id,
                user_id=session.info.get("tenant_user_id"),
                transaction_id=transaction.id,
                transaction_reference=transaction.reference,
                action="stock_adjusted",
                details=f"Stock count: {product.name}; {reason}",
                previous_value=str(expected),
                new_value=str(counted),
            )
        )
    if transaction is not None:
        transaction.total_kobo = loss_cost + gain_cost
        post_journal(
            session,
            business_id,
            transaction,
            transaction.memo,
            [
                ("6010", loss_cost, 0),
                ("1200", gain_cost, 0),
                ("1200", 0, loss_cost),
                ("4090", 0, gain_cost),
            ],
            allow_zero=True,
        )
    return stock_count


@_atomic
def reverse_transaction(
    session,
    business_id: int,
    original: Transaction,
    reason: str,
):
    reason = reason.strip()
    if not reason:
        raise ValueError("Enter a reason for reversing this transaction.")
    if original.business_id != business_id or original.status != "posted":
        raise ValueError("Only posted transactions from this business can be reversed.")
    if original.reversal_of_id is not None:
        raise ValueError("A reversal transaction cannot itself be reversed.")
    already_reversed = session.scalar(
        select(Transaction.id).where(
            Transaction.business_id == business_id,
            Transaction.reversal_of_id == original.id,
        )
    )
    if already_reversed is not None:
        raise ValueError("This transaction has already been reversed.")
    if session.scalar(
        select(ReturnRecord.id).where(
            ReturnRecord.business_id == business_id,
            ReturnRecord.original_transaction_id == original.id,
        )
    ):
        raise ValueError("A transaction with recorded returns cannot be reversed.")
    if original.journal_entry is None or not original.journal_entry.lines:
        raise ValueError("The transaction has no journal entry to reverse.")

    business = session.get(Business, business_id)
    if business is None:
        raise ValueError("Business not found.")
    movements = list(
        session.scalars(
            select(InventoryMovement).where(
                InventoryMovement.business_id == business_id,
                InventoryMovement.transaction_id == original.id,
            )
        )
    )
    reversal = _transaction(
        session,
        business_id,
        "reversal",
        original.counterparty_name,
        original.payment_method,
        original.total_kobo,
        f"Reversal of {original.reference}: {reason}",
    )
    reversal.reversal_of_id = original.id

    for movement in movements:
        quantity = abs(Decimal(movement.quantity))
        if movement.quantity < 0:
            receive_stock(
                session,
                business_id,
                session.get(Product, movement.product_id),
                quantity,
                movement.total_kobo,
                reversal.id,
                f"Reversal of {original.reference}: {reason}",
                movement_type="reversal",
            )
        else:
            cost = consume_stock(
                session,
                business_id,
                movement.product_id,
                quantity,
                business.inventory_method or "FIFO",
            )
            if cost != movement.total_kobo:
                raise ValueError(
                    "Current stock valuation differs from the original movement; "
                    "this transaction cannot be reversed safely."
                )
            session.add(
                InventoryMovement(
                    business_id=business_id,
                    transaction_id=reversal.id,
                    product_id=movement.product_id,
                    movement_type="reversal",
                    quantity=-quantity,
                    unit_cost_kobo=_unit_cost(cost, quantity),
                    total_kobo=cost,
                    reason=f"Reversal of {original.reference}: {reason}",
                )
            )

    reversal_lines = [
        (line.account.code, line.credit_kobo, line.debit_kobo)
        for line in original.journal_entry.lines
    ]
    post_journal(
        session,
        business_id,
        reversal,
        reversal.memo,
        reversal_lines,
        allow_zero=True,
    )
    previous_status = original.status
    original.status = "reversed"
    session.add(
        AuditLog(
            business_id=business_id,
            user_id=session.info.get("tenant_user_id"),
            transaction_id=reversal.id,
            transaction_reference=reversal.reference,
            action="transaction_reversed",
            details=f"Reversed {original.reference}: {reason}",
            previous_value=previous_status,
            new_value="reversed",
        )
    )
    return reversal


def _prior_return_allocations(session, original_items, return_type):
    allocations = {
        item.id: {"quantity": Decimal(0), "amount": 0, "cost": 0}
        for item in original_items
    }
    if not original_items:
        return allocations
    original = original_items[0].transaction
    records = list(
        session.scalars(
            select(ReturnRecord).where(
                ReturnRecord.business_id == original.business_id,
                ReturnRecord.original_transaction_id == original.id,
                ReturnRecord.return_type == return_type,
                ReturnRecord.product_id == original_items[0].product_id,
            )
        )
    )
    if not records:
        return allocations

    return_item_costs = {}
    return_item_ids = [record.return_item_id for record in records if record.return_item_id]
    if return_item_ids:
        return_item_costs = {
            item.id: item.cost_kobo
            for item in session.scalars(
                select(TransactionItem).where(TransactionItem.id.in_(return_item_ids))
            )
        }
    legacy_transaction_ids = {
        record.return_transaction_id
        for record in records
        if record.return_item_id is None
    }
    legacy_costs = {}
    if legacy_transaction_ids:
        legacy_costs = dict(
            session.execute(
                select(
                    TransactionItem.transaction_id,
                    func.sum(TransactionItem.cost_kobo),
                )
                .where(
                    TransactionItem.business_id == original.business_id,
                    TransactionItem.transaction_id.in_(legacy_transaction_ids),
                    TransactionItem.product_id == original_items[0].product_id,
                )
                .group_by(TransactionItem.transaction_id)
            ).all()
        )

    legacy_quantity = Decimal(0)
    legacy_amount = 0
    legacy_cost = 0
    for record in records:
        if record.original_item_id is not None:
            allocation = allocations.get(record.original_item_id)
            if allocation is not None:
                allocation["quantity"] += Decimal(record.quantity)
                allocation["amount"] += record.amount_kobo
                allocation["cost"] += return_item_costs.get(record.return_item_id, 0)
        else:
            legacy_quantity += Decimal(record.quantity)
            legacy_amount += record.amount_kobo
            legacy_cost += legacy_costs.get(record.return_transaction_id, 0)

    for item in original_items:
        if legacy_quantity <= 0:
            break
        line_remaining = max(
            Decimal(item.quantity) - allocations[item.id]["quantity"], Decimal(0)
        )
        taken = min(legacy_quantity, line_remaining)
        if taken <= 0:
            continue
        amount = (
            legacy_amount
            if taken == legacy_quantity
            else int(
                (Decimal(legacy_amount) * taken / legacy_quantity).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
        )
        cost = (
            legacy_cost
            if taken == legacy_quantity
            else int(
                (Decimal(legacy_cost) * taken / legacy_quantity).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
        )
        allocations[item.id]["quantity"] += taken
        allocations[item.id]["amount"] += amount
        allocations[item.id]["cost"] += cost
        legacy_quantity -= taken
        legacy_amount -= amount
        legacy_cost -= cost
    return allocations


def _make_return_allocations(session, original_items, quantity, return_type):
    prior = _prior_return_allocations(session, original_items, return_type)
    remaining = quantity
    allocations = []
    for item in original_items:
        previous = prior[item.id]
        available = Decimal(item.quantity) - previous["quantity"]
        if available <= 0:
            continue
        take = min(available, remaining)
        remaining_amount = item.total_kobo - previous["amount"]
        remaining_cost = item.cost_kobo - previous["cost"]
        if remaining_amount < 0 or remaining_cost < 0:
            raise ValueError("Recorded returns exceed the source transaction line.")
        amount = (
            remaining_amount
            if take == available
            else int(
                (Decimal(remaining_amount) * take / available).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
        )
        cost = (
            remaining_cost
            if take == available
            else int(
                (Decimal(remaining_cost) * take / available).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
        )
        allocations.append((item, take, amount, cost))
        remaining -= take
        if remaining == 0:
            break
    if remaining > 0:
        raise ValueError("Return quantity exceeds the unreturned transaction quantity.")
    return allocations


def _return_product_and_items(session, business_id, original, product_or_item):
    if isinstance(product_or_item, TransactionItem):
        if (
            product_or_item.business_id != business_id
            or product_or_item.transaction_id != original.id
        ):
            raise ValueError("The selected line is not part of this transaction.")
        product_id = product_or_item.product_id
        selected_item_id = product_or_item.id
    else:
        if product_or_item.business_id != business_id:
            raise ValueError("The product belongs to a different business.")
        product_id = product_or_item.id
        selected_item_id = None
    product = session.get(Product, product_id)
    if product is None or product.business_id != business_id:
        raise ValueError("The product belongs to a different business.")
    statement = select(TransactionItem).where(
        TransactionItem.business_id == business_id,
        TransactionItem.transaction_id == original.id,
        TransactionItem.product_id == product.id,
    )
    if selected_item_id is not None:
        statement = statement.where(TransactionItem.id == selected_item_id)
    original_items = list(session.scalars(statement.order_by(TransactionItem.id)))
    if not original_items:
        raise ValueError("That product was not on the original transaction.")
    return product, original_items


def _audit_return(session, business_id, original, transaction):
    session.add(
        AuditLog(
            business_id=business_id,
            user_id=session.info.get("tenant_user_id"),
            transaction_id=transaction.id,
            transaction_reference=transaction.reference,
            action="transaction_reversed",
            details=f"Return against original transaction {original.reference}.",
            previous_value=f"{original.status}|{original.total_kobo}",
            new_value=f"{transaction.status}|{transaction.total_kobo}",
        )
    )


@_atomic
def record_sales_return(
    session,
    business_id: int,
    original: Transaction,
    product: Product | TransactionItem,
    quantity,
    refund_method="cash",
    reason="",
):
    if original.transaction_type != "sale" or original.business_id != business_id:
        raise ValueError("Choose a sale from this business.")
    product, original_items = _return_product_and_items(
        session, business_id, original, product
    )
    quantity = normalize_quantity(product, quantity)
    allocations = _make_return_allocations(
        session, original_items, quantity, "sales"
    )
    refund = sum(amount for _, _, amount, _ in allocations)
    inventory_cost = sum(cost for _, _, _, cost in allocations)
    credit_reversal = 0
    if original.credit_amount_kobo:
        already_credited = session.scalar(
            select(func.coalesce(func.sum(Transaction.credit_amount_kobo), 0))
            .join(
                ReturnRecord,
                ReturnRecord.return_transaction_id == Transaction.id,
            )
            .where(
                ReturnRecord.business_id == business_id,
                ReturnRecord.original_transaction_id == original.id,
                ReturnRecord.return_type == "sales",
                Transaction.status == "posted",
            )
        ) or 0
        credit_limit = max(0, original.credit_amount_kobo - int(already_credited))
        allocated_credit = (
            min(credit_limit, refund)
            if refund == original.total_kobo
            else min(
                credit_limit,
                int(
                    (
                        Decimal(original.credit_amount_kobo)
                        * refund
                        / original.total_kobo
                    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                ),
            )
        )
        outstanding = max(
            0,
            -_party_balance(
                session, business_id, original.counterparty_name, "customer"
            ),
        )
        credit_reversal = min(outstanding, allocated_credit)
    cash_refund = refund - credit_reversal
    if cash_refund and refund_method not in ("cash", "bank", "pos", "transfer"):
        raise ValueError("Choose a valid cash, bank or POS refund method.")
    return_method = (
        "credit"
        if cash_refund == 0
        else refund_method
        if credit_reversal == 0
        else "split"
    )
    transaction = _transaction(
        session,
        business_id,
        "sales_return",
        original.counterparty_name,
        return_method,
        refund,
        f"Sales return: {reason}",
    )
    transaction.credit_amount_kobo = credit_reversal
    transaction.customer_id = original.customer_id
    for source_item, returned_quantity, amount, cost in allocations:
        returned_item = TransactionItem(
            business_id=business_id,
            transaction_id=transaction.id,
            product_id=product.id,
            description=source_item.description,
            quantity=returned_quantity,
            unit_price_kobo=int(
                (Decimal(amount) / returned_quantity).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            ),
            total_kobo=amount,
            cost_kobo=cost,
        )
        session.add(returned_item)
        session.flush()
        receive_stock(
            session,
            business_id,
            product,
            returned_quantity,
            cost,
            transaction.id,
            "Returned goods",
            movement_type="sales_return",
        )
        session.add(
            ReturnRecord(
                business_id=business_id,
                original_transaction_id=original.id,
                return_transaction_id=transaction.id,
                product_id=product.id,
                original_item_id=source_item.id,
                return_item_id=returned_item.id,
                return_type="sales",
                quantity=returned_quantity,
                amount_kobo=amount,
                reason=reason,
            )
        )
    credit_lines = []
    if credit_reversal:
        credit_lines.append(("1100", 0, credit_reversal))
    if cash_refund:
        credit_lines.append((_method_account(refund_method), 0, cash_refund))
    post_journal(
        session,
        business_id,
        transaction,
        transaction.memo,
        [
            ("4100", refund, 0),
            *credit_lines,
            ("1200", inventory_cost, 0),
            ("5000", 0, inventory_cost),
        ],
    )
    _audit_return(session, business_id, original, transaction)
    return transaction


@_atomic
def record_purchase_return(
    session,
    business_id: int,
    original: Transaction,
    product: Product | TransactionItem,
    quantity,
    reason="",
    refund_method="cash",
):
    if (
        original.transaction_type != "purchase"
        or original.business_id != business_id
        or original.status != "posted"
    ):
        raise ValueError("Choose a posted purchase from this business.")
    product, original_items = _return_product_and_items(
        session, business_id, original, product
    )
    quantity = normalize_quantity(product, quantity)
    if refund_method not in ("cash", "bank", "pos", "transfer"):
        raise ValueError("Choose a valid refund method.")
    allocations = _make_return_allocations(
        session, original_items, quantity, "purchase"
    )
    business = session.get(Business, business_id)
    if business is None:
        raise ValueError("Business not found.")
    inventory_costs = [
        consume_purchase_batch(
            session,
            business_id,
            product.id,
            returned_quantity,
            original.id,
            business.inventory_method or "FIFO",
            purchase_item_id=source_item.id,
        )
        for source_item, returned_quantity, _, _ in allocations
    ]
    refund = sum(amount for _, _, amount, _ in allocations)
    inventory_cost = sum(inventory_costs)
    credit_reversal = 0
    if original.credit_amount_kobo:
        already_credited = session.scalar(
            select(func.coalesce(func.sum(Transaction.credit_amount_kobo), 0))
            .join(
                ReturnRecord,
                ReturnRecord.return_transaction_id == Transaction.id,
            )
            .where(
                ReturnRecord.business_id == business_id,
                ReturnRecord.original_transaction_id == original.id,
                ReturnRecord.return_type == "purchase",
                Transaction.status == "posted",
            )
        ) or 0
        credit_limit = max(
            0, original.credit_amount_kobo - int(already_credited)
        )
        allocated_credit = (
            min(credit_limit, refund)
            if refund == original.total_kobo
            else min(
                credit_limit,
                int(
                    (
                        Decimal(original.credit_amount_kobo)
                        * refund
                        / original.total_kobo
                    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                ),
            )
        )
        outstanding = max(
            0,
            _party_balance(
                session, business_id, original.counterparty_name, "supplier"
            ),
        )
        credit_reversal = min(outstanding, allocated_credit)
    cash_refund = refund - credit_reversal
    payment_method = (
        "credit"
        if cash_refund == 0
        else refund_method
        if credit_reversal == 0
        else "split"
    )
    transaction = _transaction(
        session,
        business_id,
        "purchase_return",
        original.counterparty_name,
        payment_method,
        refund,
        f"Purchase return: {reason}",
    )
    transaction.credit_amount_kobo = credit_reversal
    transaction.supplier_id = original.supplier_id
    for (source_item, returned_quantity, amount, _), cost in zip(
        allocations, inventory_costs
    ):
        returned_item = TransactionItem(
            business_id=business_id,
            transaction_id=transaction.id,
            product_id=product.id,
            description=source_item.description,
            quantity=returned_quantity,
            unit_price_kobo=int(
                (Decimal(amount) / returned_quantity).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            ),
            total_kobo=amount,
            cost_kobo=cost,
        )
        session.add(returned_item)
        session.flush()
        session.add(
            InventoryMovement(
                business_id=business_id,
                transaction_id=transaction.id,
                product_id=product.id,
                movement_type="purchase_return",
                quantity=-returned_quantity,
                unit_cost_kobo=int(
                    (Decimal(cost) / returned_quantity).quantize(
                        Decimal("1"), rounding=ROUND_HALF_UP
                    )
                ),
                total_kobo=cost,
                reason=reason or "Returned to supplier",
            )
        )
        session.add(
            ReturnRecord(
                business_id=business_id,
                original_transaction_id=original.id,
                return_transaction_id=transaction.id,
                product_id=product.id,
                original_item_id=source_item.id,
                return_item_id=returned_item.id,
                return_type="purchase",
                quantity=returned_quantity,
                amount_kobo=amount,
                reason=reason,
            )
        )
    debit_lines = []
    if credit_reversal:
        debit_lines.append(("2000", credit_reversal, 0))
    if cash_refund:
        debit_lines.append((_method_account(refund_method), cash_refund, 0))
    lines = [*debit_lines, ("1200", 0, inventory_cost)]
    if refund > inventory_cost:
        lines.append(("4090", 0, refund - inventory_cost))
    elif inventory_cost > refund:
        lines.append(("6010", inventory_cost - refund, 0))
    post_journal(session, business_id, transaction, transaction.memo, lines)
    _audit_return(session, business_id, original, transaction)
    return transaction
