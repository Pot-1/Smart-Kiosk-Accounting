from functools import wraps

from sqlalchemy import func, select

from database.models import (
    Business,
    Customer,
    Expense,
    InventoryMovement,
    Payment,
    Product,
    ReceiptAttachment,
    ReturnRecord,
    StockAdjustment,
    Supplier,
    Transaction,
    TransactionItem,
)
from inventory.stock_manager import consume_purchase_batch, consume_stock, receive_stock
from accounting.journal_engine import post_journal
from utils.currency import format_naira
from utils.helpers import lagos_now, new_reference


def _atomic(function):
    @wraps(function)
    def wrapped(session, *args, **kwargs):
        with session.begin_nested():
            return function(session, *args, **kwargs)

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
        memo=memo,
        status="posted",
    )
    session.add(transaction)
    session.flush()
    return transaction


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
        sale_types = ("sale", "sales_return")
        payment_types = ("customer_payment",)
    else:
        sale_types = ("purchase", "purchase_return", "expense")
        payment_types = ("supplier_payment",)
    charge_sums = {}
    for kind in sale_types:
        charge_sums[kind] = session.scalar(
            select(func.coalesce(func.sum(Transaction.total_kobo), 0)).where(
                Transaction.business_id == business_id,
                Transaction.counterparty_name == party_name,
                Transaction.transaction_type == kind,
                Transaction.payment_method == "credit",
            )
        ) or 0
    charged = charge_sums[sale_types[0]] - charge_sums[sale_types[1]]
    if party_type == "supplier":
        charged += charge_sums[sale_types[2]]
    paid = session.scalar(
        select(func.coalesce(func.sum(Transaction.total_kobo), 0)).where(
            Transaction.business_id == business_id,
            Transaction.counterparty_name == party_name,
            Transaction.transaction_type.in_(payment_types),
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
):
    items = list(items)
    if not items:
        raise ValueError("Add at least one product to the purchase.")
    if not supplier_name.strip():
        raise ValueError("Enter a supplier name.")
    if transport_kobo < 0:
        raise ValueError("Transport cost cannot be negative.")
    if payment_method not in ("cash", "bank", "pos", "credit", "transfer"):
        raise ValueError("Choose cash, bank, POS or credit for this purchase.")
    lines = []
    base_total = 0
    for product, quantity, unit_cost_kobo in items:
        if product.business_id != business_id:
            raise ValueError("A purchase item belongs to a different business.")
        if quantity <= 0 or unit_cost_kobo <= 0:
            raise ValueError("Purchase quantity and unit cost must be greater than zero.")
        line_total = quantity * unit_cost_kobo
        lines.append([product, quantity, unit_cost_kobo, line_total, 0])
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
    transaction.supplier_id = _ensure_supplier(session, business_id, supplier_name).id
    _attach_receipt(session, business_id, transaction, receipt_image, receipt_name)
    for product, quantity, unit_cost, line_total, freight in lines:
        landed_cost = line_total + freight
        session.add(
            TransactionItem(
                transaction_id=transaction.id,
                product_id=product.id,
                description=product.name,
                quantity=quantity,
                unit_price_kobo=unit_cost,
                total_kobo=line_total,
                cost_kobo=landed_cost,
            )
        )
        receive_stock(session, business_id, product, quantity, landed_cost, transaction.id)
    credit_code = "2000" if payment_method == "credit" else _method_account(payment_method)
    post_journal(
        session, business_id, transaction, transaction.memo,
        [("1200", total, 0), (credit_code, 0, total)],
    )
    return transaction


@_atomic
def record_sale(
    session, business_id: int, items, payment_method="cash", customer_name="",
    receipt_image: bytes | None = None, receipt_name: str = "",
):
    items = list(items)
    if not items:
        raise ValueError("Add at least one product to the sale.")
    if payment_method not in ("cash", "bank", "pos", "credit", "transfer"):
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
        if quantity <= 0 or unit_price_kobo <= 0:
            raise ValueError("Sale quantity and selling price must be greater than zero.")
        cost = consume_stock(session, business_id, product.id, quantity, costing_method)
        amount = quantity * unit_price_kobo
        sale_lines.append((product, quantity, unit_price_kobo, amount, cost))
        total += amount
        cost_total += cost
    transaction = _transaction(
        session, business_id, "sale", customer_name, payment_method, total, "Sale"
    )
    if payment_method == "credit" and not customer_name.strip():
        raise ValueError("Enter a customer name for a credit sale.")
    if customer_name.strip():
        transaction.customer_id = _ensure_customer(session, business_id, customer_name).id
    _attach_receipt(session, business_id, transaction, receipt_image, receipt_name)
    for product, quantity, unit_price, amount, cost in sale_lines:
        session.add(
            TransactionItem(
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
                unit_cost_kobo=(cost + quantity // 2) // quantity,
                total_kobo=cost,
                reason="Goods sold",
            )
        )
    debit_code = _method_account(payment_method)
    post_journal(
        session, business_id, transaction, "Sale",
        [(debit_code, total, 0), ("4000", 0, total),
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
    if quantity <= 0:
        raise ValueError("Adjustment quantity must be greater than zero.")
    if reason not in {"missing", "stolen", "spoiled", "damaged", "supplier_shortage"}:
        raise ValueError("Choose a supported stock-loss reason.")
    if product.business_id != business_id:
        raise ValueError("This product belongs to a different business.")
    business = session.get(Business, business_id)
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
            unit_cost_kobo=(cost + quantity // 2) // quantity, total_kobo=cost,
            reason=notes or reason.replace("_", " "),
        )
    )
    session.add(
        StockAdjustment(
            business_id=business_id, transaction_id=transaction.id,
            product_id=product.id, quantity=quantity,
            unit_cost_kobo=(cost + quantity // 2) // quantity,
            total_cost_kobo=cost,
            reason=reason, notes=notes,
        )
    )
    post_journal(
        session, business_id, transaction, transaction.memo,
        [("6010", cost, 0), ("1200", 0, cost)],
    )
    return transaction


@_atomic
def record_sales_return(
    session, business_id: int, original: Transaction, product: Product,
    quantity: int, refund_method="cash", reason="",
):
    if original.transaction_type != "sale" or quantity <= 0:
        raise ValueError("Choose a sale and enter a positive return quantity.")
    if original.business_id != business_id or product.business_id != business_id:
        raise ValueError("The sale or product belongs to a different business.")
    if original.payment_method != "credit" and refund_method not in ("cash", "bank", "pos", "transfer"):
        raise ValueError("Choose a valid cash, bank or POS refund method.")
    original_items = list(session.scalars(
        select(TransactionItem).where(
            TransactionItem.transaction_id == original.id,
            TransactionItem.product_id == product.id,
        )
    ))
    prior = sum(row.quantity for row in original_items)
    already_returned = sum(
        row.quantity for row in session.scalars(
            select(ReturnRecord).where(
                ReturnRecord.original_transaction_id == original.id,
                ReturnRecord.return_type == "sales",
                ReturnRecord.product_id == product.id,
            )
        )
    )
    if quantity > prior - already_returned:
        raise ValueError("Return quantity exceeds the unreturned sale quantity.")
    sale_item_qty = sum(row.quantity for row in original_items)
    original_sale_total = sum(row.total_kobo for row in original_items)
    original_cost_total = sum(row.cost_kobo for row in original_items)
    refund = (original_sale_total * quantity + sale_item_qty // 2) // sale_item_qty
    inventory_cost = (original_cost_total * quantity + sale_item_qty // 2) // sale_item_qty
    original_unit_price = (
        original_sale_total + sale_item_qty // 2
    ) // sale_item_qty
    transaction = _transaction(
        session, business_id, "sales_return", original.counterparty_name,
        "credit" if original.payment_method == "credit" else refund_method,
        refund, f"Sales return: {reason}",
    )
    transaction.customer_id = original.customer_id
    session.add(
        TransactionItem(
            transaction_id=transaction.id, product_id=product.id,
            description=product.name, quantity=quantity,
            unit_price_kobo=original_unit_price,
            total_kobo=refund, cost_kobo=inventory_cost,
        )
    )
    receive_stock(session, business_id, product, quantity, inventory_cost, transaction.id,
                  "Returned goods", movement_type="sales_return")
    debit_code = "1100" if original.payment_method == "credit" else _method_account(refund_method)
    post_journal(
        session, business_id, transaction, transaction.memo,
        [("4100", refund, 0), (debit_code, 0, refund),
         ("1200", inventory_cost, 0), ("5000", 0, inventory_cost)],
    )
    session.add(
        ReturnRecord(
            business_id=business_id, original_transaction_id=original.id,
            return_transaction_id=transaction.id, return_type="sales",
            product_id=product.id,
            quantity=quantity, amount_kobo=refund, reason=reason,
        )
    )
    return transaction


@_atomic
def record_purchase_return(
    session, business_id: int, original: Transaction, product: Product,
    quantity: int, reason="", refund_method="cash",
):
    if original.transaction_type != "purchase" or quantity <= 0:
        raise ValueError("Choose a purchase and enter a positive return quantity.")
    if original.business_id != business_id or product.business_id != business_id:
        raise ValueError("The purchase or product belongs to a different business.")
    original_items = list(session.scalars(
        select(TransactionItem).where(
            TransactionItem.transaction_id == original.id,
            TransactionItem.product_id == product.id,
        )
    ))
    if not original_items:
        raise ValueError("That product was not on the original purchase.")
    original_quantity = sum(row.quantity for row in original_items)
    returned = session.scalar(
        select(func.coalesce(func.sum(ReturnRecord.quantity), 0)).where(
            ReturnRecord.original_transaction_id == original.id,
            ReturnRecord.return_type == "purchase",
            ReturnRecord.product_id == product.id,
        )
    ) or 0
    if quantity > original_quantity - returned:
        raise ValueError("Return quantity exceeds the unreturned purchase quantity.")
    inventory_cost = consume_purchase_batch(
        session, business_id, product.id, quantity, original.id
    )
    payment_method = "credit" if original.payment_method == "credit" else refund_method
    if payment_method not in ("cash", "bank", "pos", "credit"):
        raise ValueError("Choose a valid refund method.")
    transaction = _transaction(
        session, business_id, "purchase_return", original.counterparty_name,
        payment_method, inventory_cost, f"Purchase return: {reason}",
    )
    transaction.supplier_id = original.supplier_id
    session.add(
        TransactionItem(
            transaction_id=transaction.id, product_id=product.id,
            description=product.name, quantity=quantity,
            unit_price_kobo=(
                sum(row.total_kobo for row in original_items) + original_quantity // 2
            ) // original_quantity,
            total_kobo=inventory_cost, cost_kobo=inventory_cost,
        )
    )
    session.add(
        InventoryMovement(
            business_id=business_id, transaction_id=transaction.id,
            product_id=product.id, movement_type="purchase_return",
            quantity=-quantity, unit_cost_kobo=(inventory_cost + quantity // 2) // quantity,
            total_kobo=inventory_cost, reason=reason or "Returned to supplier",
        )
    )
    debit_code = "2000" if payment_method == "credit" else _method_account(payment_method)
    post_journal(
        session, business_id, transaction, transaction.memo,
        [(debit_code, inventory_cost, 0), ("1200", 0, inventory_cost)],
    )
    session.add(
        ReturnRecord(
            business_id=business_id, original_transaction_id=original.id,
            return_transaction_id=transaction.id, product_id=product.id,
            return_type="purchase", quantity=quantity,
            amount_kobo=inventory_cost, reason=reason,
        )
    )
    return transaction
