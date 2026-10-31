from sqlalchemy import select

from database.models import Product
from inventory.stock_manager import consume_stock, receive_stock, stock_quantity, stock_value
from tests.support import DatabaseTestCase


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

    def test_fifo_and_lifo_use_the_expected_lot_costs(self):
        fifo_product = self._new_product("FIFO-TEST")
        receive_stock(self.db, self.business.id, fifo_product, 2, 2_000)
        receive_stock(self.db, self.business.id, fifo_product, 3, 6_000)
        self.assertEqual(
            consume_stock(self.db, self.business.id, fifo_product.id, 2, "FIFO"),
            2_000,
        )

        lifo_product = self._new_product("LIFO-TEST")
        receive_stock(self.db, self.business.id, lifo_product, 2, 2_000)
        receive_stock(self.db, self.business.id, lifo_product, 3, 6_000)
        self.assertEqual(
            consume_stock(self.db, self.business.id, lifo_product.id, 2, "LIFO"),
            4_000,
        )

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
