from sqlalchemy import select

from database.models import Account, AuditLog, JournalEntry, JournalLine
from utils.helpers import lagos_now


def account_by_code(session, business_id: int, code: str) -> Account:
    account = session.scalar(
        select(Account).where(Account.business_id == business_id, Account.code == code)
    )
    if account is None:
        raise ValueError(f"Required account {code} is missing.")
    return account


def post_journal(session, business_id: int, transaction, memo: str, lines):
    normalized = []
    debit_total = credit_total = 0
    for code, debit, credit in lines:
        debit, credit = int(debit), int(credit)
        if debit < 0 or credit < 0 or (debit and credit):
            raise ValueError("Journal lines must contain a non-negative debit or credit.")
        if debit or credit:
            normalized.append((account_by_code(session, business_id, code), debit, credit))
            debit_total += debit
            credit_total += credit
    if not normalized or debit_total != credit_total:
        raise ValueError("The journal entry is not balanced.")
    entry = JournalEntry(
        business_id=business_id,
        transaction=transaction,
        entry_date=transaction.transaction_date or lagos_now(),
        memo=memo,
    )
    entry.lines = [
        JournalLine(account=account, debit_kobo=debit, credit_kobo=credit)
        for account, debit, credit in normalized
    ]
    session.add(entry)
    session.flush()
    session.add(
        AuditLog(
            business_id=business_id,
            transaction_id=transaction.id,
            action="transaction_posted",
            details=f"{transaction.reference}: {memo}",
        )
    )
    return entry
