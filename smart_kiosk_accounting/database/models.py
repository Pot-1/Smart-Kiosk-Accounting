from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from utils.helpers import lagos_now


class Base(DeclarativeBase):
    pass


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    currency: Mapped[str] = mapped_column(String(3), default="NGN")
    timezone: Mapped[str] = mapped_column(String(40), default="Africa/Lagos")
    inventory_method: Mapped[str] = mapped_column(String(20), default="FIFO")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ProductCategory(Base):
    __tablename__ = "product_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    name: Mapped[str] = mapped_column(String(100))
    __table_args__ = (UniqueConstraint("business_id", "name"),)


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(40), default="")
    opening_balance_kobo: Mapped[int] = mapped_column(Integer, default=0)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(40), default="")
    opening_balance_kobo: Mapped[int] = mapped_column(Integer, default=0)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("product_categories.id"))
    sku: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(160))
    unit: Mapped[str] = mapped_column(String(30), default="pieces")
    selling_price_kobo: Mapped[int] = mapped_column(Integer, default=0)
    reorder_level: Mapped[int] = mapped_column(Integer, default=5)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("business_id", "sku"),
        CheckConstraint("selling_price_kobo >= 0", name="nonnegative_product_price"),
        CheckConstraint("reorder_level >= 0", name="nonnegative_reorder_level"),
    )


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    code: Mapped[str] = mapped_column(String(12))
    name: Mapped[str] = mapped_column(String(120))
    account_type: Mapped[str] = mapped_column(String(20))
    __table_args__ = (
        UniqueConstraint("business_id", "code"),
        CheckConstraint(
            "account_type IN ('asset','liability','equity','revenue','expense')",
            name="valid_account_type",
        ),
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    reference: Mapped[str] = mapped_column(String(40), unique=True)
    transaction_type: Mapped[str] = mapped_column(String(30))
    transaction_date: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    counterparty_name: Mapped[str] = mapped_column(String(160), default="")
    payment_method: Mapped[str] = mapped_column(String(20), default="cash")
    total_kobo: Mapped[int] = mapped_column(Integer, default=0)
    memo: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="posted")
    items: Mapped[list["TransactionItem"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )
    journal_entry: Mapped["JournalEntry | None"] = relationship(
        back_populates="transaction", uselist=False
    )
    __table_args__ = (
        CheckConstraint("total_kobo >= 0", name="nonnegative_transaction_total"),
    )


class TransactionItem(Base):
    __tablename__ = "transaction_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    description: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_kobo: Mapped[int] = mapped_column(Integer)
    total_kobo: Mapped[int] = mapped_column(Integer)
    cost_kobo: Mapped[int] = mapped_column(Integer, default=0)
    transaction: Mapped[Transaction] = relationship(back_populates="items")
    __table_args__ = (
        CheckConstraint("quantity > 0", name="positive_transaction_item_quantity"),
        CheckConstraint("unit_price_kobo >= 0", name="nonnegative_transaction_item_price"),
        CheckConstraint("total_kobo >= 0 AND cost_kobo >= 0", name="nonnegative_transaction_item_amount"),
    )


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id"), unique=True
    )
    entry_date: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    memo: Mapped[str] = mapped_column(Text)
    transaction: Mapped[Transaction | None] = relationship(back_populates="journal_entry")
    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )


class JournalLine(Base):
    __tablename__ = "journal_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    journal_entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    debit_kobo: Mapped[int] = mapped_column(Integer, default=0)
    credit_kobo: Mapped[int] = mapped_column(Integer, default=0)
    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship()
    __table_args__ = (
        CheckConstraint(
            "debit_kobo >= 0 AND credit_kobo >= 0 "
            "AND NOT (debit_kobo > 0 AND credit_kobo > 0)",
            name="valid_journal_line",
        ),
    )


class StockBatch(Base):
    __tablename__ = "stock_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    source_transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))
    quantity_received: Mapped[int] = mapped_column(Integer)
    quantity_remaining: Mapped[int] = mapped_column(Integer)
    unit_cost_kobo: Mapped[int] = mapped_column(Integer)
    total_cost_kobo: Mapped[int] = mapped_column(Integer, default=0)
    remaining_cost_kobo: Mapped[int] = mapped_column(Integer, default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    __table_args__ = (
        CheckConstraint("quantity_received > 0", name="positive_received_quantity"),
        CheckConstraint(
            "quantity_remaining >= 0 AND quantity_remaining <= quantity_received",
            name="valid_remaining_quantity",
        ),
        CheckConstraint(
            "unit_cost_kobo >= 0 AND total_cost_kobo >= 0 AND remaining_cost_kobo >= 0",
            name="nonnegative_batch_cost",
        ),
    )


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    movement_type: Mapped[str] = mapped_column(String(30))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_cost_kobo: Mapped[int] = mapped_column(Integer)
    total_kobo: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"))
    party_type: Mapped[str] = mapped_column(String(20))
    party_name: Mapped[str] = mapped_column(String(160))
    amount_kobo: Mapped[int] = mapped_column(Integer)
    payment_method: Mapped[str] = mapped_column(String(20))
    paid_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    __table_args__ = (
        CheckConstraint("amount_kobo > 0", name="positive_payment_amount"),
    )


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"))
    category: Mapped[str] = mapped_column(String(100))
    amount_kobo: Mapped[int] = mapped_column(Integer)


class ReturnRecord(Base):
    __tablename__ = "returns"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    original_transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"))
    return_transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    return_type: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[int] = mapped_column(Integer)
    amount_kobo: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)


class StockAdjustment(Base):
    __tablename__ = "stock_adjustments"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_cost_kobo: Mapped[int] = mapped_column(Integer)
    total_cost_kobo: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(30))
    notes: Mapped[str] = mapped_column(Text, default="")


class BankStatement(Base):
    __tablename__ = "bank_statements"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    transaction_date: Mapped[datetime] = mapped_column(DateTime)
    description: Mapped[str] = mapped_column(String(200))
    amount_kobo: Mapped[int] = mapped_column(Integer)
    matched_transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"))
    action: Mapped[str] = mapped_column(String(80))
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)


class ReceiptAttachment(Base):
    __tablename__ = "receipt_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id"), unique=True
    )
    file_name: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(50), default="image/jpeg")
    image_data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
