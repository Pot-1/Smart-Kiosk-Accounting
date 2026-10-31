from datetime import date
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import event, select

from database.models import StockCountLine
from database.models import Product
from inventory.stock_manager import (
    consume_stock,
    inventory_reconciliation,
    receive_stock,
    stock_quantity,
    stock_value,
)
from tests.support import DatabaseTestCase
from transactions.service import record_stock_count


class InventoryTests(DatabaseTestCase):
    def test_sample_inventory_reconciles_to_ledger(self):
        self.assertEqual(stock_quantity(self.db, self.product.id), 90)
        self.assertEqual(stock_value(self.db, self.business.id, self.product.id), 73_350_000)

    def _new_product(self, sku):
        product = Product(
            business_id=self.business.id, sku=sku, name=sku,
            selling_price_kobo=1_000, reorder_level=0,
        )
        self.db.add(product)
        self.db.flush()
        return product

    def test_fifo_costing_is_supported_and_lifo_is_rejected(self):
        fifo_product = self._new_product("FIFO-TEST")
        receive_stock(self.db, self.business.id, fifo_product, 2, 2_000)
        receive_stock(self.db, self.business.id, fifo_product, 3, 6_000)
        self.assertEqual(
            consume_stock(self.db, self.business.id, fifo_product.id, 2, "FIFO"),
            2_000,
        )

        lifo_product = self._new_product("LIFO-TEST")
        receive_stock(self.db, self.business.id, lifo_product, 2, 2_000)
        with self.assertRaisesRegex(ValueError, "LIFO costing is unsupported"):
            consume_stock(self.db, self.business.id, lifo_product.id, 1, "LIFO")

    def test_fractional_weight_stock_uses_fefo_picking_and_fifo_costing(self):
        product = self._new_product("FROZEN-KG")
        product.unit = "kg"
        received_first = receive_stock(
            self.db,
            self.business.id,
            product,
            1,
            1_000,
            expires_on=date(2027, 1, 1),
        )
        expires_first = receive_stock(
            self.db,
            self.business.id,
            product,
            1,
            3_000,
            expires_on=date(2026, 12, 1),
        )
        self.assertEqual(
            consume_stock(self.db, self.business.id, product.id, Decimal("0.5"), "FIFO"),
            500,
        )
        self.db.refresh(received_first)
        self.db.refresh(expires_first)
        self.assertEqual(received_first.quantity_remaining, Decimal("1.000"))
        self.assertEqual(expires_first.quantity_remaining, Decimal("0.500"))
        self.assertEqual(stock_quantity(self.db, product.id), Decimal("1.500"))
        self.assertEqual(stock_value(self.db, self.business.id, product.id), 3_500)

    def test_weighted_average_cost_and_remaining_value(self):
        product = self._new_product("AVG-TEST")
        receive_stock(self.db, self.business.id, product, 2, 2_000)
        receive_stock(self.db, self.business.id, product, 2, 6_000)
        self.assertEqual(
            consume_stock(self.db, self.business.id, product.id, 2, "WEIGHTED_AVERAGE"),
            4_000,
        )
        self.assertEqual(stock_quantity(self.db, product.id), 2)
        self.assertEqual(stock_value(self.db, self.business.id, product.id), 4_000)

    def test_physical_stock_count_posts_discrepancy_and_is_idempotent(self):
        expected = stock_quantity(self.db, self.product.id)
        count = record_stock_count(
            self.db,
            self.business.id,
            [(self.product, expected - 1, "damaged", 0)],
            "Freezer count",
            idempotency_key="weekly-count-1",
        )
        repeated = record_stock_count(
            self.db,
            self.business.id,
            [(self.product, expected - 1, "damaged", 0)],
            "Freezer count",
            idempotency_key="weekly-count-1",
        )
        self.assertEqual(count.id, repeated.id)
        self.assertEqual(stock_quantity(self.db, self.product.id), expected - 1)
        line = self.db.scalar(
            select(StockCountLine).where(StockCountLine.stock_count_id == count.id)
        )
        self.assertEqual(line.variance, Decimal("-1.000"))

    def test_inventory_movement_and_value_subledgers_reconcile(self):
        reconciliation = inventory_reconciliation(self.db, self.business.id)
        self.assertTrue(reconciliation["movement_quantities_match"])
        self.assertTrue(reconciliation["movement_values_match"])
        self.assertTrue(reconciliation["inventory_ledger_matches"])

    def test_product_stock_frame_uses_constant_number_of_queries(self):
        from pages.common import product_stock_frame

        for index in range(5):
            product = self._new_product(f"AGGREGATE-{index}")
            receive_stock(self.db, self.business.id, product, 2, 1_000)
        self.db.flush()
        selects = []

        def capture_select(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture_select)
        try:
            with patch("pages.common.current_business_id", return_value=self.business.id):
                frame = product_stock_frame(self.db)
        finally:
            event.remove(self.engine, "before_cursor_execute", capture_select)

        self.assertEqual(len(selects), 2)
        row = frame.loc[frame["SKU"] == "AGGREGATE-0"].iloc[0]
        self.assertEqual(row["On hand"], Decimal("2.000"))
        self.assertEqual(row["Stock value"], "₦10.00")

    def test_inventory_reconciliation_uses_constant_number_of_queries(self):
        for index in range(5):
            product = self._new_product(f"RECONCILE-{index}")
            receive_stock(self.db, self.business.id, product, 2, 1_000)
        self.db.flush()
        selects = []

        def capture_select(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture_select)
        try:
            reconciliation = inventory_reconciliation(self.db, self.business.id)
        finally:
            event.remove(self.engine, "before_cursor_execute", capture_select)

        self.assertEqual(len(selects), 5)
        self.assertTrue(reconciliation["movement_quantities_match"])
        self.assertTrue(reconciliation["movement_values_match"])
