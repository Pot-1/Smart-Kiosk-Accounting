from sqlalchemy import func, select

from accounting.balance_sheet import balance_sheet
from accounting.trial_balance import trial_balance_rows
from database.models import Account, JournalEntry, JournalLine
from inventory.stock_manager import stock_value
from tests.support import DatabaseTestCase


class AccountingTests(DatabaseTestCase):
    def test_trial_balance_and_every_journal_entry_balance(self):
        rows = trial_balance_rows(self.db, self.business.id)
        self.assertEqual(sum(row[3] for row in rows), sum(row[4] for row in rows))
        for entry in self.db.scalars(select(JournalEntry)):
            debit, credit = self.db.execute(
                select(
                    func.sum(JournalLine.debit_kobo),
                    func.sum(JournalLine.credit_kobo),
                ).where(JournalLine.journal_entry_id == entry.id)
            ).one()
            self.assertEqual(debit, credit)

    def test_balance_sheet_equation_includes_current_profit(self):
        result = balance_sheet(self.db, self.business.id)
        self.assertTrue(result["balances"])
        self.assertEqual(result["assets"], result["liabilities"] + result["equity"])

    def test_inventory_asset_matches_remaining_stock_value(self):
        account = self.db.scalar(
            select(Account).where(
                Account.business_id == self.business.id, Account.code == "1200"
            )
        )
        debit, credit = self.db.execute(
            select(
                func.sum(JournalLine.debit_kobo),
                func.sum(JournalLine.credit_kobo),
            ).where(JournalLine.account_id == account.id)
        ).one()
        self.assertEqual(
            debit - credit, stock_value(self.db, self.business.id)
        )
