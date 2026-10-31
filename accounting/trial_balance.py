from sqlalchemy import func, select

from database.models import Account, JournalLine


def trial_balance_rows(session, business_id: int):
    return session.execute(
        select(
            Account.code,
            Account.name,
            Account.account_type,
            func.coalesce(func.sum(JournalLine.debit_kobo), 0),
            func.coalesce(func.sum(JournalLine.credit_kobo), 0),
        )
        .outerjoin(JournalLine, JournalLine.account_id == Account.id)
        .where(Account.business_id == business_id)
        .group_by(Account.id)
        .order_by(Account.code)
    ).all()
