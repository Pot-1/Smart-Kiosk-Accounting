import unittest

from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import (
    Account,
    AuditLog,
    BankStatement,
    Base,
    Business,
    BusinessMembership,
    Customer,
    Expense,
    InventoryMovement,
    JournalEntry,
    JournalLine,
    Payment,
    Product,
    ProductCategory,
    ReceiptAttachment,
    ReturnRecord,
    StockAdjustment,
    StockBatch,
    StockCount,
    StockCountLine,
    Supplier,
    Transaction,
    TransactionItem,
    User,
)
from database.tenant import TenantSession
from transactions.service import record_sale
from utils.authorization import seed_role_permissions
from utils.helpers import lagos_now


class MultiTenantIsolationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(
            bind=self.engine, class_=TenantSession, expire_on_commit=False
        )
        with self.Session() as session:
            session.info["system_operation"] = True
            self.business_a = Business(name="Tenant A")
            self.business_b = Business(name="Tenant B")
            session.add_all((self.business_a, self.business_b))
            session.flush()
            self.users = [
                User(
                    business_id=self.business_a.id,
                    username="owner-a",
                    password_hash="hash",
                    full_name="Owner A",
                    email="owner-a@example.com",
                ),
                User(
                    business_id=self.business_a.id,
                    username="cashier-a",
                    password_hash="hash",
                    full_name="Cashier A",
                    email="cashier-a@example.com",
                ),
                User(
                    business_id=self.business_b.id,
                    username="owner-b",
                    password_hash="hash",
                    full_name="Owner B",
                    email="owner-b@example.com",
                ),
            ]
            session.add_all(self.users)
            session.flush()
            session.add_all(
                [
                    BusinessMembership(
                        user_id=self.users[0].id,
                        business_id=self.business_a.id,
                        username="owner-a",
                        role="OWNER",
                    ),
                    BusinessMembership(
                        user_id=self.users[1].id,
                        business_id=self.business_a.id,
                        username="cashier-a",
                        role="CASHIER",
                    ),
                    BusinessMembership(
                        user_id=self.users[0].id,
                        business_id=self.business_b.id,
                        username="owner-a",
                        role="MANAGER",
                    ),
                    BusinessMembership(
                        user_id=self.users[2].id,
                        business_id=self.business_b.id,
                        username="owner-b",
                        role="OWNER",
                    ),
                ]
            )
            seed_role_permissions(session, self.business_a.id)
            seed_role_permissions(session, self.business_b.id)
            self.records = {}
            for business in (self.business_a, self.business_b):
                suffix = "a" if business.id == self.business_a.id else "b"
                category = ProductCategory(
                    business_id=business.id, name=f"Category {suffix}"
                )
                customer = Customer(
                    business_id=business.id, name=f"Customer {suffix}"
                )
                supplier = Supplier(
                    business_id=business.id, name=f"Supplier {suffix}"
                )
                account = Account(
                    business_id=business.id,
                    code="1010",
                    name=f"Cash {suffix}",
                    account_type="asset",
                )
                session.add_all((category, customer, supplier, account))
                session.flush()
                product = Product(
                    business_id=business.id,
                    category_id=category.id,
                    sku=f"SKU-{suffix}",
                    name=f"Product {suffix}",
                    selling_price_kobo=100,
                )
                transaction = Transaction(
                    business_id=business.id,
                    customer_id=customer.id,
                    supplier_id=supplier.id,
                    reference=f"SALE-{suffix}",
                    transaction_type="sale",
                    transaction_date=lagos_now(),
                    total_kobo=100,
                )
                session.add_all((product, transaction))
                session.flush()
                item = TransactionItem(
                    business_id=business.id,
                    transaction_id=transaction.id,
                    product_id=product.id,
                    description=product.name,
                    quantity=1,
                    unit_price_kobo=100,
                    total_kobo=100,
                )
                entry = JournalEntry(
                    business_id=business.id,
                    transaction_id=transaction.id,
                    memo="Test journal",
                )
                session.add_all((item, entry))
                session.flush()
                line = JournalLine(
                    business_id=business.id,
                    journal_entry_id=entry.id,
                    account_id=account.id,
                    debit_kobo=100,
                )
                batch = StockBatch(
                    business_id=business.id,
                    product_id=product.id,
                    quantity_received=1,
                    quantity_remaining=1,
                    valuation_quantity_remaining=1,
                    unit_cost_kobo=50,
                )
                movement = InventoryMovement(
                    business_id=business.id,
                    transaction_id=transaction.id,
                    product_id=product.id,
                    movement_type="receipt",
                    quantity=1,
                    unit_cost_kobo=50,
                    total_kobo=50,
                )
                audit = AuditLog(
                    business_id=business.id,
                    user_id=(
                        self.users[0].id
                        if business.id == self.business_a.id
                        else self.users[2].id
                    ),
                    transaction_id=transaction.id,
                    transaction_reference=transaction.reference,
                    action="test",
                )
                receipt = ReceiptAttachment(
                    business_id=business.id,
                    transaction_id=transaction.id,
                    file_name=f"{suffix}.jpg",
                    image_data=b"receipt",
                )
                session.add_all((line, batch, movement, audit, receipt))
                self.records[business.id] = {
                    "customer": customer,
                    "supplier": supplier,
                    "product": product,
                    "transaction": transaction,
                    "item": item,
                    "account": account,
                    "entry": entry,
                    "line": line,
                    "batch": batch,
                    "movement": movement,
                    "audit": audit,
                    "receipt": receipt,
                }
            session.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_scoped_queries_and_identity_map_exclude_other_business_records(self):
        a = self.records[self.business_a.id]
        b = self.records[self.business_b.id]
        with self.Session() as session:
            session.info["system_operation"] = True
            cached_other_product = session.get(Product, b["product"].id)
            session.info.clear()
            session.info.update(
                tenant_user_id=self.users[1].id,
                tenant_business_id=self.business_a.id,
            )
            self.assertIsNone(session.get(Product, cached_other_product.id))
            for model_name in (
                "customer",
                "supplier",
                "product",
                "transaction",
                "item",
                "account",
                "entry",
                "line",
                "batch",
                "movement",
                "audit",
                "receipt",
            ):
                model = type(a[model_name])
                self.assertIsNotNone(session.get(model, a[model_name].id))
                self.assertIsNone(session.get(model, b[model_name].id))
                self.assertEqual(
                    session.scalar(
                        select(model).where(model.id == b[model_name].id)
                    ),
                    None,
                    model_name,
                )

    def test_unscoped_tenant_query_is_denied(self):
        with self.Session() as session:
            with self.assertRaisesRegex(PermissionError, "business context"):
                session.scalars(select(Product)).all()

    def test_service_rejects_an_unassigned_business(self):
        record_b = self.records[self.business_b.id]
        with self.Session() as session:
            session.info.update(
                tenant_user_id=self.users[1].id,
                tenant_business_id=self.business_a.id,
            )
            with self.assertRaisesRegex(PermissionError, "active business"):
                record_sale(
                    session,
                    self.business_b.id,
                    [(record_b["product"], 1, 100)],
                    "cash",
                )

    def test_foreign_key_rejects_cross_business_customer_reference(self):
        customer_b = self.records[self.business_b.id]["customer"]
        with self.Session() as session:
            session.info["system_operation"] = True
            invalid = Transaction(
                business_id=self.business_a.id,
                customer_id=customer_b.id,
                reference="CROSS-TENANT",
                transaction_type="sale",
            )
            session.add(invalid)
            with self.assertRaises(IntegrityError):
                session.flush()

    def test_composite_foreign_keys_reject_cross_business_module_references(self):
        a = self.records[self.business_a.id]
        b = self.records[self.business_b.id]
        invalid_rows = [
            (
                "transaction item product",
                TransactionItem(
                    business_id=self.business_a.id,
                    transaction_id=a["transaction"].id,
                    product_id=b["product"].id,
                    description="Cross-business product",
                    quantity=1,
                    unit_price_kobo=100,
                    total_kobo=100,
                ),
            ),
            (
                "journal entry transaction",
                JournalEntry(
                    business_id=self.business_a.id,
                    transaction_id=b["transaction"].id,
                    memo="Cross-business transaction",
                ),
            ),
            (
                "journal line account",
                JournalLine(
                    business_id=self.business_a.id,
                    journal_entry_id=a["entry"].id,
                    account_id=b["account"].id,
                    debit_kobo=1,
                ),
            ),
            (
                "stock batch product",
                StockBatch(
                    business_id=self.business_a.id,
                    product_id=b["product"].id,
                    quantity_received=1,
                    quantity_remaining=1,
                    valuation_quantity_remaining=1,
                    unit_cost_kobo=1,
                ),
            ),
            (
                "stock batch source transaction",
                StockBatch(
                    business_id=self.business_a.id,
                    product_id=a["product"].id,
                    source_transaction_id=b["transaction"].id,
                    quantity_received=1,
                    quantity_remaining=1,
                    valuation_quantity_remaining=1,
                    unit_cost_kobo=1,
                ),
            ),
            (
                "inventory movement product",
                InventoryMovement(
                    business_id=self.business_a.id,
                    transaction_id=a["transaction"].id,
                    product_id=b["product"].id,
                    movement_type="test",
                    quantity=1,
                    unit_cost_kobo=1,
                    total_kobo=1,
                ),
            ),
            (
                "payment transaction",
                Payment(
                    business_id=self.business_a.id,
                    transaction_id=b["transaction"].id,
                    party_type="supplier",
                    party_name="Cross-business",
                    amount_kobo=1,
                    payment_method="cash",
                ),
            ),
            (
                "expense transaction",
                Expense(
                    business_id=self.business_a.id,
                    transaction_id=b["transaction"].id,
                    category="Test",
                    amount_kobo=1,
                ),
            ),
            (
                "return original transaction",
                ReturnRecord(
                    business_id=self.business_a.id,
                    original_transaction_id=b["transaction"].id,
                    return_transaction_id=a["transaction"].id,
                    product_id=a["product"].id,
                    return_type="sales",
                    quantity=1,
                    amount_kobo=1,
                    reason="Isolation test",
                ),
            ),
            (
                "stock adjustment product",
                StockAdjustment(
                    business_id=self.business_a.id,
                    transaction_id=a["transaction"].id,
                    product_id=b["product"].id,
                    quantity=1,
                    unit_cost_kobo=1,
                    reason="missing",
                ),
            ),
            (
                "bank statement matched transaction",
                BankStatement(
                    business_id=self.business_a.id,
                    transaction_date=lagos_now(),
                    description="Cross-business match",
                    amount_kobo=1,
                    matched_transaction_id=b["transaction"].id,
                ),
            ),
            (
                "audit log transaction",
                AuditLog(
                    business_id=self.business_a.id,
                    user_id=self.users[1].id,
                    transaction_id=b["transaction"].id,
                    action="test",
                ),
            ),
            (
                "receipt transaction",
                ReceiptAttachment(
                    business_id=self.business_a.id,
                    transaction_id=b["transaction"].id,
                    file_name="cross-business.jpg",
                    image_data=b"synthetic",
                ),
            ),
            (
                "product category",
                Product(
                    business_id=self.business_a.id,
                    category_id=b["product"].category_id,
                    sku="CROSS-CATEGORY",
                    name="Cross-business category",
                    selling_price_kobo=1,
                ),
            ),
            (
                "stock count transaction",
                StockCount(
                    business_id=self.business_a.id,
                    transaction_id=b["transaction"].id,
                    reason="Cross-business transaction",
                ),
            ),
        ]
        for label, invalid_row in invalid_rows:
            with self.subTest(reference=label):
                with self.Session() as session:
                    session.info["system_operation"] = True
                    session.add(invalid_row)
                    with self.assertRaises(IntegrityError):
                        session.flush()

        with self.subTest(reference="stock count line product"):
            with self.Session() as session:
                session.info["system_operation"] = True
                count = StockCount(
                    business_id=self.business_a.id,
                    reason="Tenant isolation test",
                )
                session.add(count)
                session.flush()
                session.add(
                    StockCountLine(
                        business_id=self.business_a.id,
                        stock_count_id=count.id,
                        product_id=b["product"].id,
                        expected_quantity=0,
                        counted_quantity=0,
                        variance=0,
                    )
                )
                with self.assertRaises(IntegrityError):
                    session.flush()


if __name__ == "__main__":
    unittest.main()
