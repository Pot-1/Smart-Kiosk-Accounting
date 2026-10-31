from sqlalchemy import func, select

from database.models import Account, Customer, JournalLine, Supplier, Transaction


def _ledger_balance(session, business_id: int, code: str, normal_side: str) -> int:
    account = session.scalar(
        select(Account).where(
            Account.business_id == business_id,
            Account.code == code,
        )
    )
    if account is None:
        raise ValueError(f"Required reconciliation account {code} is missing.")
    debit, credit = session.execute(
        select(
            func.coalesce(func.sum(JournalLine.debit_kobo), 0),
            func.coalesce(func.sum(JournalLine.credit_kobo), 0),
        ).where(
            JournalLine.business_id == business_id,
            JournalLine.account_id == account.id,
        )
    ).one()
    return int(debit - credit if normal_side == "debit" else credit - debit)


def party_balance_reconciliation(session, business_id: int) -> dict:
    customers = list(
        session.scalars(
            select(Customer).where(Customer.business_id == business_id)
        )
    )
    suppliers = list(
        session.scalars(
            select(Supplier).where(Supplier.business_id == business_id)
        )
    )
    party_names = {party.name for party in (*customers, *suppliers)}
    party_totals = {
        (name, transaction_type): (int(credit or 0), int(total or 0))
        for name, transaction_type, credit, total in session.execute(
            select(
                Transaction.counterparty_name,
                Transaction.transaction_type,
                func.coalesce(func.sum(Transaction.credit_amount_kobo), 0),
                func.coalesce(func.sum(Transaction.total_kobo), 0),
            )
            .where(
                Transaction.business_id == business_id,
                Transaction.status == "posted",
                Transaction.counterparty_name.in_(party_names),
                Transaction.transaction_type.in_(
                    (
                        "sale",
                        "sales_return",
                        "customer_payment",
                        "purchase",
                        "purchase_return",
                        "expense",
                        "supplier_payment",
                    )
                ),
            )
            .group_by(
                Transaction.counterparty_name,
                Transaction.transaction_type,
            )
        )
    }
    customer_subledger = sum(
        party_totals.get((customer.name, "sale"), (0, 0))[0]
        - party_totals.get((customer.name, "sales_return"), (0, 0))[0]
        - party_totals.get((customer.name, "customer_payment"), (0, 0))[1]
        for customer in customers
    )
    supplier_subledger = sum(
        party_totals.get((supplier.name, "purchase"), (0, 0))[0]
        + party_totals.get((supplier.name, "expense"), (0, 0))[0]
        - party_totals.get((supplier.name, "purchase_return"), (0, 0))[0]
        - party_totals.get((supplier.name, "supplier_payment"), (0, 0))[1]
        for supplier in suppliers
    )
    customer_ledger = _ledger_balance(session, business_id, "1100", "debit")
    supplier_ledger = _ledger_balance(session, business_id, "2000", "credit")
    return {
        "customer_subledger_kobo": customer_subledger,
        "customer_ledger_kobo": customer_ledger,
        "customers_match": customer_subledger == customer_ledger,
        "supplier_subledger_kobo": supplier_subledger,
        "supplier_ledger_kobo": supplier_ledger,
        "suppliers_match": supplier_subledger == supplier_ledger,
    }
