from sqlalchemy import select

from database.models import Account, AuditLog, JournalEntry, JournalLine
from database.tenant import require_business_access
from utils.helpers import lagos_now


def account_by_code(session, business_id: int, code: str) -> Account:
    require_business_access(session, business_id)
    account = session.scalar(
        select(Account).where(Account.business_id == business_id, Account.code == code)
    )
    if account is None:
        raise ValueError(f"Required account {code} is missing.")
    return account


def post_journal(
    session, business_id: int, transaction, memo: str, lines, *, allow_zero=False
):
    require_business_access(session, business_id)
    if transaction.business_id != business_id:
        raise ValueError("A journal entry must use its transaction's business.")
    if transaction.status != "posted":
        raise ValueError("Only posted transactions can receive journal entries.")
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
    if not normalized and allow_zero and lines:
        normalized.append((account_by_code(session, business_id, lines[0][0]), 0, 0))
    if not normalized or debit_total != credit_total:
        raise ValueError("The journal entry is not balanced.")
    entry = JournalEntry(
        business_id=business_id,
        transaction=transaction,
        entry_date=transaction.transaction_date or lagos_now(),
        memo=memo,
        created_by_user_id=session.info.get("tenant_user_id"),
    )
    entry.lines = [
        JournalLine(
            business_id=business_id,
            account=account,
            debit_kobo=debit,
            credit_kobo=credit,
        )
        for account, debit, credit in normalized
    ]
    session.add(entry)
    session.flush()
    session.add(
        AuditLog(
            business_id=business_id,
            user_id=session.info.get("tenant_user_id"),
            transaction_id=transaction.id,
            transaction_reference=transaction.reference,
            action="transaction_posted",
            details=f"{transaction.reference}: {memo}",
        )
    )
    return entry
