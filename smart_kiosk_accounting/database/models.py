from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    Numeric,
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
    name: Mapped[str] = mapped_column(String(160), index=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    currency: Mapped[str] = mapped_column(String(3), default="NGN")
    timezone: Mapped[str] = mapped_column(String(40), default="Africa/Lagos")
    inventory_method: Mapped[str] = mapped_column(String(20), default="FIFO")
    business_type: Mapped[str] = mapped_column(String(80), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    legal_structure: Mapped[str] = mapped_column(String(60), default="")
    onboarding_step: Mapped[int] = mapped_column(Integer, default=1)
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    opening_capital_kobo: Mapped[int] = mapped_column(Integer, default=0)
    opening_cash_kobo: Mapped[int] = mapped_column(Integer, default=0)
    opening_bank_kobo: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lagos_now, onupdate=lagos_now
    )
    creator: Mapped["User | None"] = relationship(
        foreign_keys=[created_by_user_id], back_populates="created_businesses"
    )
    memberships: Mapped[list["BusinessMembership"]] = relationship(
        back_populates="business"
    )
    __table_args__ = (UniqueConstraint("id", "name"),)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int | None] = mapped_column(
        ForeignKey("businesses.id"), nullable=True
    )
    username: Mapped[str] = mapped_column(String(80), index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    full_name: Mapped[str] = mapped_column(String(160), default="")
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    phone: Mapped[str] = mapped_column(String(40), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lagos_now, onupdate=lagos_now
    )
    memberships: Mapped[list["BusinessMembership"]] = relationship(
        foreign_keys="BusinessMembership.user_id",
        back_populates="user",
    )
    created_businesses: Mapped[list[Business]] = relationship(
        foreign_keys=[Business.created_by_user_id],
        back_populates="creator",
    )


class BusinessMembership(Base):
    __tablename__ = "business_memberships"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    username: Mapped[str | None] = mapped_column(String(80), nullable=True)
    role: Mapped[str] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    invited_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lagos_now, onupdate=lagos_now
    )
    user: Mapped[User] = relationship(
        foreign_keys=[user_id], back_populates="memberships"
    )
    business: Mapped[Business] = relationship(back_populates="memberships")
    __table_args__ = (
        UniqueConstraint("user_id", "business_id"),
        UniqueConstraint("business_id", "username"),
        CheckConstraint(
            "role IN ('OWNER','MANAGER','CASHIER','ACCOUNTANT','STOCK_MANAGER')",
            name="valid_business_role",
        ),
    )


RoleAssignment = BusinessMembership


class RolePermission(Base):
    __tablename__ = "role_permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    role: Mapped[str] = mapped_column(String(30))
    permission: Mapped[str] = mapped_column(String(60))
    allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lagos_now, onupdate=lagos_now
    )
    __table_args__ = (
        UniqueConstraint("business_id", "role", "permission"),
        UniqueConstraint("business_id", "id"),
        ForeignKeyConstraint(
            ["updated_by_user_id", "business_id"],
            ["business_memberships.user_id", "business_memberships.business_id"],
            name="fk_role_permission_updated_by_membership",
        ),
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remember_device: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        UniqueConstraint("business_id", "user_id", "id"),
        ForeignKeyConstraint(
            ["user_id", "business_id"],
            ["business_memberships.user_id", "business_memberships.business_id"],
            name="fk_auth_session_membership",
        ),
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuthEvent(Base):
    __tablename__ = "auth_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    identifier_hash: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)


class ProductCategory(Base):
    __tablename__ = "product_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    __table_args__ = (
        UniqueConstraint("business_id", "name"),
        UniqueConstraint("business_id", "id"),
    )


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(40), default="")
    opening_balance_kobo: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    __table_args__ = (
        UniqueConstraint("business_id", "name"),
        UniqueConstraint("business_id", "id"),
    )


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(40), default="")
    opening_balance_kobo: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    __table_args__ = (
        UniqueConstraint("business_id", "name"),
        UniqueConstraint("business_id", "id"),
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    category_id: Mapped[int | None] = mapped_column(Integer)
    sku: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(160))
    unit: Mapped[str] = mapped_column(String(30), default="pieces")
    selling_price_kobo: Mapped[int] = mapped_column(Integer, default=0)
    reorder_level: Mapped[int] = mapped_column(Integer, default=5)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("business_id", "sku"),
        UniqueConstraint("business_id", "id"),
        ForeignKeyConstraint(
            ["business_id", "category_id"],
            ["product_categories.business_id", "product_categories.id"],
            name="fk_product_category_business",
        ),
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
        UniqueConstraint("business_id", "id"),
        CheckConstraint(
            "account_type IN ('asset','liability','equity','revenue','expense')",
            name="valid_account_type",
        ),
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    customer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supplier_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference: Mapped[str] = mapped_column(String(40))
    transaction_type: Mapped[str] = mapped_column(String(30))
    transaction_date: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    counterparty_name: Mapped[str] = mapped_column(String(160), default="")
    payment_method: Mapped[str] = mapped_column(String(20), default="cash")
    total_kobo: Mapped[int] = mapped_column(Integer, default=0)
    credit_amount_kobo: Mapped[int] = mapped_column(Integer, default=0)
    memo: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="posted")
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reversal_of_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lagos_now, onupdate=lagos_now
    )
    items: Mapped[list["TransactionItem"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )
    journal_entry: Mapped["JournalEntry | None"] = relationship(
        back_populates="transaction", uselist=False
    )
    __table_args__ = (
        UniqueConstraint("business_id", "reference"),
        UniqueConstraint("business_id", "idempotency_key"),
        UniqueConstraint("business_id", "reversal_of_id"),
        UniqueConstraint("business_id", "id"),
        ForeignKeyConstraint(
            ["business_id", "customer_id"],
            ["customers.business_id", "customers.id"],
            name="fk_transaction_customer_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "supplier_id"],
            ["suppliers.business_id", "suppliers.id"],
            name="fk_transaction_supplier_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "reversal_of_id"],
            ["transactions.business_id", "transactions.id"],
            name="fk_transaction_reversal_business",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id", "business_id"],
            ["business_memberships.user_id", "business_memberships.business_id"],
            name="fk_transaction_creator_membership",
        ),
        CheckConstraint("total_kobo >= 0", name="nonnegative_transaction_total"),
    )


class TransactionItem(Base):
    __tablename__ = "transaction_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    transaction_id: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    unit_price_kobo: Mapped[int] = mapped_column(Integer)
    total_kobo: Mapped[int] = mapped_column(Integer)
    cost_kobo: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    transaction: Mapped[Transaction] = relationship(back_populates="items")
    __table_args__ = (
        CheckConstraint("quantity > 0", name="positive_transaction_item_quantity"),
        CheckConstraint("unit_price_kobo >= 0", name="nonnegative_transaction_item_price"),
        CheckConstraint("total_kobo >= 0 AND cost_kobo >= 0", name="nonnegative_transaction_item_amount"),
        UniqueConstraint("business_id", "id"),
        ForeignKeyConstraint(
            ["business_id", "transaction_id"],
            ["transactions.business_id", "transactions.id"],
            name="fk_transaction_item_transaction_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "product_id"],
            ["products.business_id", "products.id"],
            name="fk_transaction_item_product_business",
        ),
    )


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    transaction_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entry_date: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    memo: Mapped[str] = mapped_column(Text)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    transaction: Mapped[Transaction | None] = relationship(back_populates="journal_entry")
    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )
    __table_args__ = (
        UniqueConstraint("business_id", "transaction_id"),
        UniqueConstraint("business_id", "id"),
        ForeignKeyConstraint(
            ["business_id", "transaction_id"],
            ["transactions.business_id", "transactions.id"],
            name="fk_journal_entry_transaction_business",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id", "business_id"],
            ["business_memberships.user_id", "business_memberships.business_id"],
            name="fk_journal_entry_creator_membership",
        ),
    )


class JournalLine(Base):
    __tablename__ = "journal_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    journal_entry_id: Mapped[int] = mapped_column(Integer)
    account_id: Mapped[int] = mapped_column(Integer)
    debit_kobo: Mapped[int] = mapped_column(Integer, default=0)
    credit_kobo: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship(overlaps="entry,lines")
    __table_args__ = (
        CheckConstraint(
            "debit_kobo >= 0 AND credit_kobo >= 0 "
            "AND NOT (debit_kobo > 0 AND credit_kobo > 0)",
            name="valid_journal_line",
        ),
        UniqueConstraint("business_id", "id"),
        ForeignKeyConstraint(
            ["business_id", "journal_entry_id"],
            ["journal_entries.business_id", "journal_entries.id"],
            name="fk_journal_line_entry_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "account_id"],
            ["accounts.business_id", "accounts.id"],
            name="fk_journal_line_account_business",
        ),
    )


class StockBatch(Base):
    __tablename__ = "stock_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    product_id: Mapped[int] = mapped_column(Integer)
    source_transaction_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quantity_received: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    quantity_remaining: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    valuation_quantity_remaining: Mapped[Decimal] = mapped_column(Numeric(18, 3), default=0)
    unit_cost_kobo: Mapped[int] = mapped_column(Integer)
    total_cost_kobo: Mapped[int] = mapped_column(Integer, default=0)
    remaining_cost_kobo: Mapped[int] = mapped_column(Integer, default=0)
    expires_on: Mapped[date | None] = mapped_column(nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    __table_args__ = (
        CheckConstraint("quantity_received > 0", name="positive_received_quantity"),
        CheckConstraint(
            "quantity_remaining >= 0 AND quantity_remaining <= quantity_received",
            name="valid_remaining_quantity",
        ),
        ForeignKeyConstraint(
            ["business_id", "product_id"],
            ["products.business_id", "products.id"],
            name="fk_stock_batch_product_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "source_transaction_id"],
            ["transactions.business_id", "transactions.id"],
            name="fk_stock_batch_transaction_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "source_item_id"],
            ["transaction_items.business_id", "transaction_items.id"],
            name="fk_stock_batch_source_item_business",
        ),
        UniqueConstraint("business_id", "id"),
        CheckConstraint(
            "unit_cost_kobo >= 0 AND total_cost_kobo >= 0 AND remaining_cost_kobo >= 0",
            name="nonnegative_batch_cost",
        ),
    )


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    transaction_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    product_id: Mapped[int] = mapped_column(Integer)
    movement_type: Mapped[str] = mapped_column(String(30))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    unit_cost_kobo: Mapped[int] = mapped_column(Integer)
    total_kobo: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    __table_args__ = (
        UniqueConstraint("business_id", "id"),
        ForeignKeyConstraint(
            ["business_id", "transaction_id"],
            ["transactions.business_id", "transactions.id"],
            name="fk_inventory_movement_transaction_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "product_id"],
            ["products.business_id", "products.id"],
            name="fk_inventory_movement_product_business",
        ),
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    transaction_id: Mapped[int] = mapped_column(Integer)
    party_type: Mapped[str] = mapped_column(String(20))
    party_name: Mapped[str] = mapped_column(String(160))
    amount_kobo: Mapped[int] = mapped_column(Integer)
    payment_method: Mapped[str] = mapped_column(String(20))
    paid_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    __table_args__ = (
        CheckConstraint("amount_kobo > 0", name="positive_payment_amount"),
        UniqueConstraint("business_id", "id"),
        ForeignKeyConstraint(
            ["business_id", "transaction_id"],
            ["transactions.business_id", "transactions.id"],
            name="fk_payment_transaction_business",
        ),
    )


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    transaction_id: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(100))
    amount_kobo: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    __table_args__ = (
        UniqueConstraint("business_id", "id"),
        ForeignKeyConstraint(
            ["business_id", "transaction_id"],
            ["transactions.business_id", "transactions.id"],
            name="fk_expense_transaction_business",
        ),
    )


class ReturnRecord(Base):
    __tablename__ = "returns"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    original_transaction_id: Mapped[int] = mapped_column(Integer)
    return_transaction_id: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[int] = mapped_column(Integer)
    original_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    return_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    return_type: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    amount_kobo: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    __table_args__ = (
        UniqueConstraint("business_id", "id"),
        ForeignKeyConstraint(
            ["business_id", "original_transaction_id"],
            ["transactions.business_id", "transactions.id"],
            name="fk_return_original_transaction_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "return_transaction_id"],
            ["transactions.business_id", "transactions.id"],
            name="fk_return_transaction_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "product_id"],
            ["products.business_id", "products.id"],
            name="fk_return_product_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "original_item_id"],
            ["transaction_items.business_id", "transaction_items.id"],
            name="fk_return_original_item_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "return_item_id"],
            ["transaction_items.business_id", "transaction_items.id"],
            name="fk_return_item_business",
        ),
    )


class StockAdjustment(Base):
    __tablename__ = "stock_adjustments"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    transaction_id: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    unit_cost_kobo: Mapped[int] = mapped_column(Integer)
    total_cost_kobo: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(30))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    __table_args__ = (
        UniqueConstraint("business_id", "id"),
        ForeignKeyConstraint(
            ["business_id", "transaction_id"],
            ["transactions.business_id", "transactions.id"],
            name="fk_stock_adjustment_transaction_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "product_id"],
            ["products.business_id", "products.id"],
            name="fk_stock_adjustment_product_business",
        ),
    )


class StockCount(Base):
    __tablename__ = "stock_counts"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    transaction_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    reason: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    __table_args__ = (
        UniqueConstraint("business_id", "id"),
        UniqueConstraint("business_id", "idempotency_key"),
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
    )


class StockCountLine(Base):
    __tablename__ = "stock_count_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    stock_count_id: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[int] = mapped_column(Integer)
    expected_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    counted_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    variance: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    adjustment_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    __table_args__ = (
        UniqueConstraint("business_id", "stock_count_id", "product_id"),
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
        CheckConstraint(
            "expected_quantity >= 0 AND counted_quantity >= 0",
            name="nonnegative_stock_count_quantities",
        ),
    )


class BankStatement(Base):
    __tablename__ = "bank_statements"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    transaction_date: Mapped[datetime] = mapped_column(DateTime)
    description: Mapped[str] = mapped_column(String(200))
    amount_kobo: Mapped[int] = mapped_column(Integer)
    matched_transaction_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    __table_args__ = (
        UniqueConstraint("business_id", "id"),
        ForeignKeyConstraint(
            ["business_id", "matched_transaction_id"],
            ["transactions.business_id", "transactions.id"],
            name="fk_bank_statement_transaction_business",
        ),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    transaction_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transaction_reference: Mapped[str] = mapped_column(String(40), default="")
    action: Mapped[str] = mapped_column(String(80))
    details: Mapped[str] = mapped_column(Text, default="")
    previous_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    __table_args__ = (
        UniqueConstraint("business_id", "id"),
        ForeignKeyConstraint(
            ["business_id", "transaction_id"],
            ["transactions.business_id", "transactions.id"],
            name="fk_audit_log_transaction_business",
        ),
        ForeignKeyConstraint(
            ["user_id", "business_id"],
            ["business_memberships.user_id", "business_memberships.business_id"],
            name="fk_audit_log_user_membership",
        ),
    )


class ReceiptAttachment(Base):
    __tablename__ = "receipt_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"))
    transaction_id: Mapped[int] = mapped_column(Integer)
    file_name: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(50), default="image/jpeg")
    image_data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
    __table_args__ = (
        UniqueConstraint("business_id", "transaction_id"),
        UniqueConstraint("business_id", "id"),
        ForeignKeyConstraint(
            ["business_id", "transaction_id"],
            ["transactions.business_id", "transactions.id"],
            name="fk_receipt_transaction_business",
        ),
    )


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_name: Mapped[str] = mapped_column(String(255), unique=True)
    file_path: Mapped[str] = mapped_column(String(1000))
    backend: Mapped[str] = mapped_column(String(20))
    size_bytes: Mapped[int] = mapped_column(Integer)
    checksum_sha256: Mapped[str] = mapped_column(String(64), default="")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    offsite: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lagos_now)
