import tempfile
import threading
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from accounting.balance_sheet import balance_sheet
from accounting.reconciliation import party_balance_reconciliation
from database.models import (
    Account,
    Base,
    Business,
    JournalLine,
    Product,
    Transaction,
    User,
)
from database.seed_data import seed_demo_business
from database.tenant import TenantSession
from inventory.stock_manager import (
    inventory_reconciliation,
    receive_stock,
    stock_quantity,
)
from transactions.service import record_purchase_return, record_sales_return
from tests.support import DatabaseTestCase
from transactions.service import (
    record_expense,
    record_owner_funds,
    record_purchase,
    record_sale,
    record_customer_payment,
    record_supplier_payment,
    reverse_transaction,
)


class SalesTests(DatabaseTestCase):
    def test_concurrent_sales_cannot_oversell_stock(self):
        with tempfile.TemporaryDirectory(prefix="ska-concurrent-sales-") as directory:
            database_path = f"{directory}/accounting.sqlite3"
            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False, "timeout": 1},
            )
            Base.metadata.create_all(engine)
            session_factory = sessionmaker(
                bind=engine,
                class_=TenantSession,
                expire_on_commit=False,
            )
            setup = session_factory()
            setup.info["system_operation"] = True
            with patch("database.seed_data.DEMO_MODE", True):
                seed_demo_business(setup)
            setup.commit()
            business_id = setup.scalar(select(Business.id))
            user_id = setup.scalar(select(User.id))
            product_id = setup.scalar(select(Product.id))
            initial_transaction_count = setup.scalar(
                select(func.count()).select_from(Transaction)
            )
            setup.close()

            start = threading.Barrier(2)
            outcomes = []
            outcomes_lock = threading.Lock()

            def post_sale():
                with session_factory() as session:
                    session.info.update(
                        tenant_user_id=user_id,
                        tenant_business_id=business_id,
                    )
                    product = session.get(Product, product_id)
                    start.wait(timeout=10)
                    try:
                        record_sale(
                            session,
                            business_id,
                            [(product, 60, product.selling_price_kobo)],
                            "cash",
                        )
                        session.commit()
                        outcome = "posted"
                    except ValueError:
                        session.rollback()
                        outcome = "rejected"
                    with outcomes_lock:
                        outcomes.append(outcome)

            threads = [threading.Thread(target=post_sale) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertCountEqual(outcomes, ["posted", "rejected"])

            verify = session_factory()
            verify.info.update(
                tenant_user_id=user_id,
                tenant_business_id=business_id,
            )
            self.assertEqual(stock_quantity(verify, product_id), 30)
            self.assertEqual(
                verify.scalar(select(func.count()).select_from(Transaction)),
                initial_transaction_count + 1,
            )
            verify.close()
            engine.dispose()

    def test_credit_sale_updates_receivable_and_cost_of_goods_sold(self):
        sale = self.db.scalar(
            select(Transaction).where(
                Transaction.transaction_type == "sale",
                Transaction.payment_method == "credit",
            )
        )
        self.assertIsNotNone(sale)
        self.assertEqual(sale.total_kobo, 3_000_000)
        cogs = self.db.scalar(
            select(func.sum(Transaction.total_kobo)).where(
                Transaction.transaction_type == "stock_adjustment"
            )
        )
        self.assertEqual(cogs, 1_630_000)

    def test_overselling_rolls_back_partial_inventory_changes(self):
        before_count = self.db.scalar(
            select(func.count()).select_from(Transaction)
        )
        before_quantity = 90
        with self.assertRaisesRegex(ValueError, "Not enough stock"):
            record_sale(
                self.db, self.business.id, [(self.product, 1_000, 1_000_000)],
                "cash",
            )
        self.assertEqual(before_count, self.db.scalar(
            select(func.count()).select_from(Transaction)
        ))
        from inventory.stock_manager import stock_quantity
        self.assertEqual(stock_quantity(self.db, self.product.id), before_quantity)

    def test_credit_sale_return_reverses_receivable_and_restores_stock(self):
        sale = self.db.scalar(
            select(Transaction).where(
                Transaction.transaction_type == "sale",
                Transaction.payment_method == "credit",
            )
        )
        result = record_sales_return(
            self.db, self.business.id, sale, self.product, 1, "cash", "Customer returned item"
        )
        self.db.flush()
        self.assertEqual(result.total_kobo, 1_000_000)
        from inventory.stock_manager import stock_quantity
        self.assertEqual(stock_quantity(self.db, self.product.id), 91)

    def test_purchase_return_reduces_supplier_balance_and_stock(self):
        purchase = self.db.scalar(
            select(Transaction).where(Transaction.transaction_type == "purchase")
        )
        result = record_purchase_return(
            self.db, self.business.id, purchase, self.product, 1, "Damaged carton"
        )
        self.db.flush()
        self.assertEqual(result.total_kobo, 800_000)
        from inventory.stock_manager import stock_quantity
        self.assertEqual(stock_quantity(self.db, self.product.id), 89)

    def test_purchase_return_from_split_tender_reverses_credit_and_cash_parts(self):
        purchase = record_purchase(
            self.db,
            self.business.id,
            [(self.product, 1, 100_000)],
            "Split Tender Supplier",
            payment_method="split",
            split_payments=[("cash", 40_000), ("credit", 60_000)],
        )

        returned = record_purchase_return(
            self.db,
            self.business.id,
            purchase,
            list(purchase.items)[0],
            1,
            "Damaged split-tender purchase",
        )

        self.assertEqual(returned.payment_method, "split")
        self.assertEqual(returned.credit_amount_kobo, 60_000)
        lines_by_account = {
            line.account.code: (line.debit_kobo, line.credit_kobo)
            for line in returned.journal_entry.lines
        }
        self.assertEqual(lines_by_account["2000"], (60_000, 0))
        self.assertEqual(lines_by_account["1010"], (40_000, 0))

    def test_purchase_return_after_supplier_payment_refunds_cash_not_payable(self):
        purchase = record_purchase(
            self.db,
            self.business.id,
            [(self.product, 1, 100_000)],
            "Paid Split Supplier",
            payment_method="split",
            split_payments=[("cash", 40_000), ("credit", 60_000)],
        )
        record_supplier_payment(
            self.db, self.business.id, "Paid Split Supplier", 60_000, "bank"
        )

        returned = record_purchase_return(
            self.db,
            self.business.id,
            purchase,
            list(purchase.items)[0],
            1,
            "Return after settlement",
        )

        self.assertEqual(returned.payment_method, "cash")
        self.assertEqual(returned.credit_amount_kobo, 0)
        lines_by_account = {
            line.account.code: (line.debit_kobo, line.credit_kobo)
            for line in returned.journal_entry.lines
        }
        self.assertEqual(lines_by_account["1010"], (100_000, 0))
        self.assertNotIn("2000", lines_by_account)

    def test_purchase_return_values_the_selected_fifo_receipt_layer(self):
        product = Product(
            business_id=self.business.id,
            sku="FIFO-RETURN",
            name="FIFO return item",
            selling_price_kobo=20_000,
        )
        self.db.add(product)
        self.db.flush()
        record_purchase(
            self.db,
            self.business.id,
            [(product, 2, 2_000)],
            "FIFO Supplier",
            payment_method="cash",
        )
        later_purchase = record_purchase(
            self.db,
            self.business.id,
            [(product, 2, 6_000)],
            "FIFO Supplier",
            payment_method="cash",
        )

        returned = record_purchase_return(
            self.db,
            self.business.id,
            later_purchase,
            list(later_purchase.items)[0],
            1,
            "Return from later receipt",
        )

        inventory_credit = sum(
            line.credit_kobo
            for line in returned.journal_entry.lines
            if line.account.code == "1200"
        )
        self.assertEqual(inventory_credit, 6_000)

    def test_reversed_purchase_cannot_be_returned_again(self):
        product = Product(
            business_id=self.business.id,
            sku="REVERSED-PURCHASE",
            name="Reversed purchase item",
            selling_price_kobo=20_000,
        )
        self.db.add(product)
        self.db.flush()
        receive_stock(self.db, self.business.id, product, 1, 2_000)
        purchase = record_purchase(
            self.db,
            self.business.id,
            [(product, 1, 2_000)],
            "Reversal Supplier",
            payment_method="cash",
        )
        reverse_transaction(
            self.db, self.business.id, purchase, "Duplicate purchase"
        )
        quantity_after_reversal = stock_quantity(self.db, product.id)
        self.assertEqual(quantity_after_reversal, 1)

        with self.assertRaisesRegex(ValueError, "posted purchase"):
            record_purchase_return(
                self.db,
                self.business.id,
                purchase,
                list(purchase.items)[0],
                1,
                "Attempt to return reversed purchase",
            )

        self.assertEqual(stock_quantity(self.db, product.id), quantity_after_reversal)

    def test_supplier_cannot_be_overpaid(self):
        before_count = self.db.scalar(select(func.count()).select_from(Transaction))
        with self.assertRaisesRegex(ValueError, "greater than the amount owed"):
            record_supplier_payment(
                self.db, self.business.id, "Lagos Poultry Supplier", 72_000_000
            )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(Transaction)), before_count
        )

    def test_sale_idempotency_returns_original_posting_without_second_stock_change(self):
        before_quantity = stock_quantity(self.db, self.product.id)
        first = record_sale(
            self.db,
            self.business.id,
            [(self.product, 1, 200_000)],
            "cash",
            idempotency_key="same-sale-submit",
        )
        after_first = stock_quantity(self.db, self.product.id)
        repeated = record_sale(
            self.db,
            self.business.id,
            [(self.product, 1, 200_000)],
            "cash",
            idempotency_key="same-sale-submit",
        )
        self.assertEqual(first.id, repeated.id)
        self.assertEqual(after_first, before_quantity - 1)
        self.assertEqual(stock_quantity(self.db, self.product.id), after_first)
        with self.assertRaisesRegex(ValueError, "different request"):
            record_sale(
                self.db,
                self.business.id,
                [(self.product, 1, 300_000)],
                "cash",
                idempotency_key="same-sale-submit",
            )

    def test_split_sale_posts_each_tender_and_balanced_journal(self):
        sale = record_sale(
            self.db,
            self.business.id,
            [(self.product, 1, 100_000)],
            "split",
            split_payments=[("cash", 40_000), ("pos", 60_000)],
        )
        account_codes = dict(
            self.db.execute(
                select(Account.id, Account.code).where(
                    Account.business_id == self.business.id
                )
            ).all()
        )
        totals = dict(
            self.db.execute(
                select(
                    JournalLine.account_id,
                    func.sum(JournalLine.debit_kobo - JournalLine.credit_kobo),
                )
                .where(JournalLine.journal_entry_id == sale.journal_entry.id)
                .group_by(JournalLine.account_id)
            ).all()
        )
        self.assertEqual(
            totals[next(account_id for account_id, code in account_codes.items() if code == "1010")],
            40_000,
        )
        self.assertEqual(
            totals[next(account_id for account_id, code in account_codes.items() if code == "1020")],
            60_000,
        )
        self.assertTrue(self.db.scalar(select(Transaction).where(Transaction.id == sale.id)))

    def test_returns_use_the_selected_original_price_line_and_cannot_exceed_it(self):
        sale = record_sale(
            self.db,
            self.business.id,
            [(self.product, 1, 100_000), (self.product, 1, 250_000)],
            "cash",
        )
        sale_lines = list(sale.items)
        result = record_sales_return(
            self.db,
            self.business.id,
            sale,
            sale_lines[1],
            1,
            reason="Different price line returned",
        )
        self.assertEqual(result.total_kobo, 250_000)
        with self.assertRaisesRegex(ValueError, "unreturned transaction quantity"):
            record_sales_return(
                self.db,
                self.business.id,
                sale,
                sale_lines[1],
                1,
                reason="Duplicate return",
            )

    def test_paid_credit_sale_return_refunds_money_instead_of_overcrediting_receivables(self):
        sale = record_sale(
            self.db,
            self.business.id,
            [(self.product, 1, 150_000)],
            "credit",
            "Paid return customer",
        )
        record_customer_payment(
            self.db,
            self.business.id,
            "Paid return customer",
            150_000,
            "cash",
        )
        result = record_sales_return(
            self.db,
            self.business.id,
            sale,
            list(sale.items)[0],
            1,
            "bank",
            "Refund paid sale",
        )
        self.assertEqual(result.payment_method, "bank")
        self.assertEqual(result.credit_amount_kobo, 0)

    def test_purchase_return_tracks_the_selected_original_line(self):
        product = Product(
            business_id=self.business.id,
            sku="RETURN-PRICE",
            name="Price-specific frozen fish",
            unit="pieces",
            selling_price_kobo=5_000,
        )
        self.db.add(product)
        self.db.flush()
        purchase = record_purchase(
            self.db,
            self.business.id,
            [(product, 1, 1_000), (product, 1, 2_000)],
            "Price-specific supplier",
            payment_method="cash",
        )
        result = record_purchase_return(
            self.db,
            self.business.id,
            purchase,
            list(purchase.items)[1],
            1,
            "Wrong item delivered",
        )
        self.assertEqual(result.total_kobo, 2_000)
        with self.assertRaisesRegex(ValueError, "unreturned transaction quantity"):
            record_purchase_return(
                self.db,
                self.business.id,
                purchase,
                list(purchase.items)[1],
                1,
                "Duplicate return",
            )

    def test_reversal_posts_a_contra_entry_and_keeps_original_record(self):
        expense = record_expense(
            self.db, self.business.id, "Freezer repair", 15_000, description="Repair"
        )
        reversal = reverse_transaction(
            self.db, self.business.id, expense, "Duplicate entry"
        )
        self.db.flush()
        self.assertEqual(expense.status, "reversed")
        self.assertEqual(reversal.reversal_of_id, expense.id)
        self.assertIsNotNone(expense.journal_entry)
        debit, credit = self.db.execute(
            select(
                func.sum(JournalLine.debit_kobo),
                func.sum(JournalLine.credit_kobo),
            ).where(JournalLine.journal_entry_id == reversal.journal_entry.id)
        ).one()
        self.assertEqual(debit, credit)

    def test_frozen_foods_financial_scenarios_reconcile_to_ledgers(self):
        cash_sale = record_sale(
            self.db, self.business.id, [(self.product, 1, 200_000)], "cash"
        )
        bank_sale = record_sale(
            self.db, self.business.id, [(self.product, 1, 210_000)], "bank"
        )
        pos_sale = record_sale(
            self.db, self.business.id, [(self.product, 1, 220_000)], "pos"
        )
        credit_sale = record_sale(
            self.db,
            self.business.id,
            [(self.product, 1, 230_000)],
            "credit",
            "Frozen-food customer",
        )
        split_sale = record_sale(
            self.db,
            self.business.id,
            [(self.product, 1, 240_000)],
            "split",
            split_payments=[("cash", 100_000), ("pos", 140_000)],
        )
        self.assertEqual(
            [row.transaction_type for row in (cash_sale, bank_sale, pos_sale, credit_sale, split_sale)],
            ["sale"] * 5,
        )
        record_customer_payment(
            self.db,
            self.business.id,
            "Frozen-food customer",
            230_000,
            "bank",
        )
        record_sales_return(
            self.db,
            self.business.id,
            credit_sale,
            list(credit_sale.items)[0],
            1,
            "cash",
            "Paid customer return",
        )

        purchase = record_purchase(
            self.db,
            self.business.id,
            [(self.product, 1, 250_000)],
            "Frozen-food supplier",
            payment_method="credit",
        )
        record_supplier_payment(
            self.db,
            self.business.id,
            "Frozen-food supplier",
            250_000,
            "bank",
        )
        record_purchase_return(
            self.db,
            self.business.id,
            purchase,
            list(purchase.items)[0],
            1,
            "Damaged delivery",
        )
        record_expense(
            self.db, self.business.id, "Freezer electricity", 15_000, "cash"
        )
        record_owner_funds(
            self.db, self.business.id, 100_000, "capital", "cash"
        )
        record_owner_funds(
            self.db, self.business.id, 10_000, "drawing", "bank"
        )
        from transactions.service import record_stock_adjustment, record_stock_count

        record_stock_adjustment(
            self.db, self.business.id, self.product, 1, "missing", "Freezer count"
        )
        record_stock_adjustment(
            self.db, self.business.id, self.product, 1, "damaged", "Damaged package"
        )
        record_stock_adjustment(
            self.db, self.business.id, self.product, 1, "spoiled", "Spoiled stock"
        )
        current_quantity = stock_quantity(self.db, self.product.id)
        record_stock_count(
            self.db,
            self.business.id,
            [(self.product, current_quantity - 1, "missing", 0)],
            "Closing physical count",
        )

        self.assertTrue(balance_sheet(self.db, self.business.id)["balances"])
        inventory_check = inventory_reconciliation(self.db, self.business.id)
        self.assertTrue(inventory_check["movement_quantities_match"])
        self.assertTrue(inventory_check["movement_values_match"])
        self.assertTrue(inventory_check["inventory_ledger_matches"])
        reconciliation = party_balance_reconciliation(self.db, self.business.id)
        self.assertTrue(reconciliation["customers_match"])
        self.assertTrue(reconciliation["suppliers_match"])
