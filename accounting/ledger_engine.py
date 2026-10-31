from sqlalchemy import select

from database.models import Account, JournalEntry, JournalLine


def ledger_rows(session, business_id: int, account_code: str):
    return session.execute(
        select(JournalEntry.entry_date, JournalEntry.memo, JournalLine.debit_kobo,
               JournalLine.credit_kobo, JournalEntry.transaction_id)
        .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(Account.business_id == business_id, Account.code == account_code)
        .order_by(JournalEntry.entry_date, JournalEntry.id)
    ).all()
