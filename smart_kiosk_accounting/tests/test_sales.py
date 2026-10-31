from sqlalchemy import func, select

from database.models import Transaction
from transactions.service import record_purchase_return, record_sales_return
from tests.support import DatabaseTestCase
from transactions.service import record_sale, record_supplier_payment


class SalesTests(DatabaseTestCase):
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
        self.assertEqual(result.total_kobo, 815_000)
        from inventory.stock_manager import stock_quantity
        self.assertEqual(stock_quantity(self.db, self.product.id), 89)

    def test_supplier_cannot_be_overpaid(self):
        before_count = self.db.scalar(select(func.count()).select_from(Transaction))
        with self.assertRaisesRegex(ValueError, "greater than the amount owed"):
            record_supplier_payment(
                self.db, self.business.id, "Lagos Poultry Supplier", 72_000_000
            )
        self.assertEqual(
            self.db.scalar(select(func.count()).select_from(Transaction)), before_count
        )
