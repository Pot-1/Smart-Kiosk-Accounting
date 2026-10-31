"""Add idempotent posting, fractional stock, expiry and count support."""

from alembic import op
from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    inspect,
    text,
)


revision = "0002_accounting_integrity"
down_revision = "0001_multi_tenant"
branch_labels = None
depends_on = None


def _tables():
    return set(inspect(op.get_bind()).get_table_names())


def _add_column(table, name, column_type, nullable=True, default=None):
    if table not in _tables():
        return
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns(table)}
    if name in columns:
        return
    with op.batch_alter_table(table) as batch:
        batch.add_column(
            Column(name, column_type, nullable=nullable, server_default=default)
        )


def _alter_quantity(table, column):
    if table not in _tables():
        return
    columns = {item["name"] for item in inspect(op.get_bind()).get_columns(table)}
    if column not in columns:
        return
    current_type = next(
        item["type"]
        for item in inspect(op.get_bind()).get_columns(table)
        if item["name"] == column
    )
    if isinstance(current_type, Numeric):
        return
    with op.batch_alter_table(table) as batch:
        batch.alter_column(
            column,
            existing_type=current_type,
            type_=Numeric(18, 3),
            existing_nullable=False,
        )


def upgrade():
    _add_column("transactions", "idempotency_key", String(100))
    _add_column("transactions", "request_fingerprint", String(64))
    _add_column(
        "transactions",
        "credit_amount_kobo",
        Integer(),
        nullable=False,
        default="0",
    )
    _add_column("transactions", "reversal_of_id", Integer())
    if "transactions" in _tables():
        uniques = inspect(op.get_bind()).get_unique_constraints("transactions")
        columns_by_constraint = {
            frozenset(constraint["column_names"] or ())
            for constraint in uniques
        }
        existing_fks = {
            fk["name"]
            for fk in inspect(op.get_bind()).get_foreign_keys("transactions")
            if fk["name"]
        }
        if frozenset(("business_id", "idempotency_key")) not in columns_by_constraint:
            with op.batch_alter_table("transactions") as batch:
                batch.create_unique_constraint(
                    "uq_transactions_business_idempotency",
                    ["business_id", "idempotency_key"],
                )
        if frozenset(("business_id", "reversal_of_id")) not in columns_by_constraint:
            with op.batch_alter_table("transactions") as batch:
                batch.create_unique_constraint(
                    "uq_transactions_business_reversal",
                    ["business_id", "reversal_of_id"],
                )
        if "fk_transaction_reversal_business" not in existing_fks:
            with op.batch_alter_table("transactions") as batch:
                batch.create_foreign_key(
                    "fk_transaction_reversal_business",
                    "transactions",
                    ["business_id", "reversal_of_id"],
                    ["business_id", "id"],
                )

    if "transactions" in _tables():
        op.get_bind().execute(
            text(
                "UPDATE transactions SET credit_amount_kobo = total_kobo "
                "WHERE payment_method = 'credit' AND credit_amount_kobo = 0"
            )
        )

    _add_column(
        "stock_batches",
        "valuation_quantity_remaining",
        Numeric(18, 3),
        nullable=False,
        default="0",
    )
    _add_column("stock_batches", "expires_on", Date())
    _add_column("stock_batches", "source_item_id", Integer())
    if "stock_batches" in _tables():
        op.get_bind().execute(
            text(
                "UPDATE stock_batches SET valuation_quantity_remaining = "
                "quantity_remaining WHERE valuation_quantity_remaining = 0 "
                "AND quantity_remaining > 0"
            )
        )
        op.get_bind().execute(
            text(
                "UPDATE stock_batches SET source_item_id = ("
                "SELECT MIN(ti.id) FROM transaction_items ti "
                "WHERE ti.business_id = stock_batches.business_id "
                "AND ti.transaction_id = stock_batches.source_transaction_id "
                "AND ti.product_id = stock_batches.product_id "
                "HAVING COUNT(*) = 1) "
                "WHERE source_item_id IS NULL AND source_transaction_id IS NOT NULL"
            )
        )
        existing_fks = {
            fk["name"]
            for fk in inspect(op.get_bind()).get_foreign_keys("stock_batches")
            if fk["name"]
        }
        if "fk_stock_batch_source_item_business" not in existing_fks:
            with op.batch_alter_table("stock_batches") as batch:
                batch.create_foreign_key(
                    "fk_stock_batch_source_item_business",
                    "transaction_items",
                    ["business_id", "source_item_id"],
                    ["business_id", "id"],
                )

    for table, column in (
        ("transaction_items", "quantity"),
        ("stock_batches", "quantity_received"),
        ("stock_batches", "quantity_remaining"),
        ("stock_batches", "valuation_quantity_remaining"),
        ("inventory_movements", "quantity"),
        ("returns", "quantity"),
        ("stock_adjustments", "quantity"),
    ):
        _alter_quantity(table, column)

    _add_column("returns", "original_item_id", Integer())
    _add_column("returns", "return_item_id", Integer())
    if "returns" in _tables():
        existing_fks = {
            fk["name"]
            for fk in inspect(op.get_bind()).get_foreign_keys("returns")
            if fk["name"]
        }
        with op.batch_alter_table("returns") as batch:
            if "fk_return_original_item_business" not in existing_fks:
                batch.create_foreign_key(
                    "fk_return_original_item_business",
                    "transaction_items",
                    ["business_id", "original_item_id"],
                    ["business_id", "id"],
                )
            if "fk_return_item_business" not in existing_fks:
                batch.create_foreign_key(
                    "fk_return_item_business",
                    "transaction_items",
                    ["business_id", "return_item_id"],
                    ["business_id", "id"],
                )

    if "transactions" in _tables():
        op.get_bind().execute(
            text(
                "UPDATE businesses SET inventory_method = 'FIFO' "
                "WHERE inventory_method = 'LIFO'"
            )
        )
    if "accounts" in _tables() and "businesses" in _tables():
        op.get_bind().execute(
            text(
                "INSERT INTO accounts (business_id, code, name, account_type) "
                "SELECT b.id, '4090', 'Inventory return gains', 'revenue' "
                "FROM businesses b WHERE NOT EXISTS ("
                "SELECT 1 FROM accounts a WHERE a.business_id = b.id "
                "AND a.code = '4090')"
            )
        )

    if "stock_counts" not in _tables():
        op.create_table(
            "stock_counts",
            Column("id", Integer(), primary_key=True),
            Column("business_id", Integer(), nullable=False),
            Column("transaction_id", Integer(), nullable=True),
            Column(
                "status", String(20), nullable=False, server_default="completed"
            ),
            Column("reason", Text(), nullable=False),
            Column("idempotency_key", String(100), nullable=True),
            Column("request_fingerprint", String(64), nullable=True),
            Column("created_by_user_id", Integer()),
            Column("created_at", DateTime(), nullable=False),
            ForeignKeyConstraint(
                ["business_id"], ["businesses.id"]
            ),
            ForeignKeyConstraint(
                ["created_by_user_id", "business_id"],
                ["business_memberships.user_id", "business_memberships.business_id"],
                name="fk_stock_count_creator_membership",
            ),
            ForeignKeyConstraint(
                ["business_id", "transaction_id"],
                ["transactions.business_id", "transactions.id"],
                name="fk_stock_count_transaction_business",
            ),
            UniqueConstraint("business_id", "id"),
            UniqueConstraint("business_id", "idempotency_key"),
        )
    if "stock_count_lines" not in _tables():
        op.create_table(
            "stock_count_lines",
            Column("id", Integer(), primary_key=True),
            Column("business_id", Integer(), nullable=False),
            Column("stock_count_id", Integer(), nullable=False),
            Column("product_id", Integer(), nullable=False),
            Column(
                "expected_quantity", Numeric(18, 3), nullable=False
            ),
            Column(
                "counted_quantity", Numeric(18, 3), nullable=False
            ),
            Column("variance", Numeric(18, 3), nullable=False),
            Column(
                "adjustment_reason", Text(), nullable=False, server_default=""
            ),
            Column(
                "created_at", DateTime(), nullable=False
            ),
            ForeignKeyConstraint(
                ["business_id", "stock_count_id"],
                ["stock_counts.business_id", "stock_counts.id"],
                name="fk_stock_count_line_count_business",
            ),
            ForeignKeyConstraint(
                ["business_id", "product_id"],
                ["products.business_id", "products.id"],
                name="fk_stock_count_line_product_business",
            ),
            UniqueConstraint(
                "business_id", "stock_count_id", "product_id"
            ),
            CheckConstraint(
                "expected_quantity >= 0 AND counted_quantity >= 0",
                name="nonnegative_stock_count_quantities",
            ),
        )


def downgrade():
    raise RuntimeError(
        "Accounting integrity changes are forward-only. Restore a verified backup "
        "to roll back this migration."
    )
