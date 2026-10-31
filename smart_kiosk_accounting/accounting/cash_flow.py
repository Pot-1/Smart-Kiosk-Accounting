from sqlalchemy import select

from database.models import Account, JournalEntry, JournalLine


def cash_flow_rows(session, business_id: int):
    return session.execute(
        select(JournalEntry.entry_date, JournalEntry.memo,
               JournalLine.debit_kobo, JournalLine.credit_kobo, Account.name)
        .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(Account.business_id == business_id, Account.code.in_(("1010", "1020")))
        .order_by(JournalEntry.entry_date)
    ).all()
