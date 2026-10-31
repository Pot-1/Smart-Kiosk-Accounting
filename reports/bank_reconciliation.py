from sqlalchemy import func, select

from database.models import Account, BankStatement, JournalEntry, JournalLine, Transaction


def bank_book_balance(session, business_id: int) -> int:
    account_id = session.scalar(
        select(Account.id).where(
            Account.business_id == business_id, Account.code == "1020"
        )
    )
    if account_id is None:
        return 0
    debit = session.scalar(
        select(func.coalesce(func.sum(JournalLine.debit_kobo), 0)).where(
            JournalLine.account_id == account_id
        )
    ) or 0
    credit = session.scalar(
        select(func.coalesce(func.sum(JournalLine.credit_kobo), 0)).where(
            JournalLine.account_id == account_id
        )
    ) or 0
    return int(debit - credit)


def import_bank_rows(session, business_id: int, rows):
    imported = 0
    for row in rows:
        amount = int(row["amount_kobo"])
        description = str(row["description"]).strip()
        statement_date = row["date"]
        exists = session.scalar(
            select(BankStatement.id).where(
                BankStatement.business_id == business_id,
                BankStatement.transaction_date == statement_date,
                BankStatement.description == description,
                BankStatement.amount_kobo == amount,
            )
        )
        if not exists:
            session.add(
                BankStatement(
                    business_id=business_id,
                    transaction_date=statement_date,
                    description=description,
                    amount_kobo=amount,
                )
            )
            imported += 1
    return imported


def bank_match_candidates(session, business_id: int, amount_kobo: int):
    return session.execute(
        select(Transaction, JournalEntry.entry_date)
        .join(JournalEntry, JournalEntry.transaction_id == Transaction.id)
        .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(
            Transaction.business_id == business_id,
            Account.code == "1020",
            JournalLine.debit_kobo - JournalLine.credit_kobo == amount_kobo,
        )
        .order_by(JournalEntry.entry_date.desc())
    ).all()
