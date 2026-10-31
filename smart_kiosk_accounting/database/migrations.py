from sqlalchemy import MetaData, Table, insert, inspect, select, text


_COLUMN_DEFAULTS = {
    ("businesses", "business_type"): "''",
    ("businesses", "location"): "''",
    ("businesses", "legal_structure"): "''",
    ("businesses", "onboarding_step"): "8",
    ("businesses", "onboarding_complete"): "1",
    ("businesses", "opening_capital_kobo"): "0",
    ("businesses", "opening_cash_kobo"): "0",
    ("businesses", "opening_bank_kobo"): "0",
    ("users", "full_name"): "''",
    ("users", "email"): "NULL",
    ("users", "phone"): "''",
    ("users", "failed_login_attempts"): "0",
    ("users", "locked_until"): "NULL",
    ("users", "is_active"): "1",
    ("stock_adjustments", "total_cost_kobo"): "0",
    ("backup_records", "checksum_sha256"): "''",
    ("businesses", "created_at"): "'1970-01-01 00:00:00'",
    ("businesses", "updated_at"): "'1970-01-01 00:00:00'",
    ("users", "created_at"): "'1970-01-01 00:00:00'",
    ("users", "updated_at"): "'1970-01-01 00:00:00'",
    ("transactions", "updated_at"): "'1970-01-01 00:00:00'",
    ("transactions", "credit_amount_kobo"): "0",
    ("role_permissions", "updated_at"): "'1970-01-01 00:00:00'",
    ("transaction_items", "created_at"): "'1970-01-01 00:00:00'",
    ("journal_entries", "created_at"): "'1970-01-01 00:00:00'",
    ("journal_lines", "created_at"): "'1970-01-01 00:00:00'",
    ("customers", "created_at"): "'1970-01-01 00:00:00'",
    ("suppliers", "created_at"): "'1970-01-01 00:00:00'",
    ("product_categories", "created_at"): "'1970-01-01 00:00:00'",
}

_REBUILD_TABLES = ("businesses", "users", "transactions")


def _add_missing_columns(connection, metadata) -> None:
    inspector = inspect(connection)
    for table_name in inspector.get_table_names():
        if table_name not in metadata.tables:
            continue
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        table = metadata.tables[table_name]
        for column in table.columns:
            if column.name in existing:
                continue
            sql_type = column.type.compile(dialect=connection.dialect)
            default = _COLUMN_DEFAULTS.get((table_name, column.name))
            declaration = f"ALTER TABLE {table_name} ADD COLUMN {column.name} {sql_type}"
            if default is not None:
                declaration += f" DEFAULT {default}"
            connection.execute(text(declaration))


def _backfill_tenant_columns(connection) -> None:
    tables = set(inspect(connection).get_table_names())
    if "transactions" in tables:
        transaction_columns = {
            column["name"] for column in inspect(connection).get_columns("transactions")
        }
        if {
            "credit_amount_kobo",
            "payment_method",
            "total_kobo",
        }.issubset(transaction_columns):
            connection.execute(
                text(
                    "UPDATE transactions SET credit_amount_kobo = "
                    "CASE WHEN payment_method = 'credit' "
                    "THEN COALESCE(total_kobo, 0) ELSE 0 END "
                    "WHERE credit_amount_kobo IS NULL OR "
                    "(payment_method = 'credit' AND credit_amount_kobo = 0)"
                )
            )
    if "transaction_items" in tables:
        connection.execute(
            text(
                "UPDATE transaction_items SET business_id = "
                "(SELECT business_id FROM transactions "
                "WHERE transactions.id = transaction_items.transaction_id) "
                "WHERE business_id IS NULL"
            )
        )
    if "journal_lines" in tables:
        connection.execute(
            text(
                "UPDATE journal_lines SET business_id = "
                "(SELECT business_id FROM journal_entries "
                "WHERE journal_entries.id = journal_lines.journal_entry_id) "
                "WHERE business_id IS NULL"
            )
        )
    if "businesses" in tables and "users" in tables:
        connection.execute(
            text(
                "UPDATE businesses SET created_by_user_id = "
                "(SELECT id FROM users WHERE users.business_id = businesses.id "
                "ORDER BY id LIMIT 1) WHERE created_by_user_id IS NULL"
            )
        )
    if "users" in tables:
        connection.execute(
            text(
                "UPDATE users SET created_at = COALESCE(created_at, "
                "'1970-01-01 00:00:00'), updated_at = COALESCE(updated_at, "
                "created_at, '1970-01-01 00:00:00')"
            )
        )
    for table_name in (
        "businesses",
        "transactions",
        "transaction_items",
        "journal_entries",
        "journal_lines",
        "customers",
        "suppliers",
        "product_categories",
        "role_permissions",
    ):
        if table_name not in tables:
            continue
        columns = {column["name"] for column in inspect(connection).get_columns(table_name)}
        if "created_at" in columns:
            updated = "updated_at = COALESCE(updated_at, created_at, '1970-01-01 00:00:00'), " if "updated_at" in columns else ""
            connection.execute(
                text(
                    f"UPDATE {table_name} SET created_at = "
                    "COALESCE(created_at, '1970-01-01 00:00:00'), "
                    f"{updated}"
                    "created_at = COALESCE(created_at, '1970-01-01 00:00:00')"
                )
            )


def _copy_table(connection, model_table, old_name: str, new_name: str) -> None:
    from database.models import Base

    metadata = MetaData()
    for source_table in Base.metadata.tables.values():
        source_table.to_metadata(metadata)
    replacement = metadata.tables[model_table.name].to_metadata(
        metadata, name=new_name
    )
    replacement.create(connection)
    old_table = Table(old_name, MetaData(), autoload_with=connection)
    old_columns = {column.name for column in old_table.columns}
    copied_columns = [
        column.name for column in replacement.columns if column.name in old_columns
    ]
    if copied_columns:
        connection.execute(
            insert(replacement).from_select(
                copied_columns,
                select(*(old_table.c[name] for name in copied_columns)),
            )
        )
    connection.execute(text(f"DROP TABLE {old_name}"))
    connection.execute(text(f"ALTER TABLE {new_name} RENAME TO {old_name}"))
    for index in model_table.indexes:
        index.create(connection, checkfirst=True)


def _migrate_memberships(connection) -> None:
    tables = set(inspect(connection).get_table_names())
    if "role_assignments" in tables:
        legacy = Table("role_assignments", MetaData(), autoload_with=connection)
        legacy_columns = {column.name for column in legacy.columns}
        if {"user_id", "business_id", "role"}.issubset(legacy_columns):
            active_expression = (
                "COALESCE(ra.is_active, 1)"
                if "is_active" in legacy_columns else "1"
            )
            created_expression = (
                "COALESCE(ra.created_at, '1970-01-01 00:00:00')"
                if "created_at" in legacy_columns else "'1970-01-01 00:00:00'"
            )
            updated_expression = (
                "COALESCE(ra.updated_at, '1970-01-01 00:00:00')"
                if "updated_at" in legacy_columns else "'1970-01-01 00:00:00'"
            )
            insert_prefix = (
                "INSERT OR IGNORE"
                if connection.dialect.name == "sqlite" else "INSERT"
            )
            conflict_clause = (
                ""
                if connection.dialect.name == "sqlite"
                else " ON CONFLICT (user_id, business_id) DO NOTHING"
            )
            connection.execute(
                text(
                    f"{insert_prefix} INTO business_memberships "
                    "(user_id, business_id, username, role, is_active, created_at, updated_at) "
                    "SELECT ra.user_id, ra.business_id, u.username, ra.role, "
                    f"{active_expression}, {created_expression}, "
                    f"{updated_expression} "
                    "FROM role_assignments ra JOIN users u ON u.id = ra.user_id"
                    f"{conflict_clause}"
                )
            )
        missing_rows = connection.scalar(
            text(
                "SELECT COUNT(*) FROM role_assignments ra "
                "LEFT JOIN business_memberships bm "
                "ON bm.user_id = ra.user_id AND bm.business_id = ra.business_id "
                "WHERE bm.id IS NULL"
            )
        )
        if missing_rows:
            raise RuntimeError(
                "Legacy membership rows could not all be copied; migration stopped."
            )
        connection.execute(text("DROP TABLE role_assignments"))
    insert_prefix = (
        "INSERT OR IGNORE"
        if connection.dialect.name == "sqlite" else "INSERT"
    )
    conflict_clause = (
        ""
        if connection.dialect.name == "sqlite"
        else " ON CONFLICT (user_id, business_id) DO NOTHING"
    )
    connection.execute(
        text(
            f"{insert_prefix} INTO business_memberships "
            "(user_id, business_id, username, role, is_active, created_at, updated_at) "
            "SELECT id, business_id, username, 'OWNER', 1, "
            "COALESCE(created_at, '1970-01-01 00:00:00'), "
            "COALESCE(updated_at, '1970-01-01 00:00:00') "
            "FROM users WHERE business_id IS NOT NULL"
            f"{conflict_clause}"
        )
    )
    connection.execute(
        text(
            "UPDATE business_memberships SET username = "
            "(SELECT username FROM users WHERE users.id = business_memberships.user_id) "
            "WHERE username IS NULL OR username = ''"
        )
    )


def _install_tenant_triggers(connection) -> None:
    table_names = set(inspect(connection).get_table_names())
    relationships = {
        "auth_sessions": (("user_id", "business_memberships", "user_id"),),
        "role_permissions": (
            ("updated_by_user_id", "business_memberships", "user_id"),
        ),
        "transactions": (
            ("customer_id", "customers", "id"),
            ("supplier_id", "suppliers", "id"),
            ("created_by_user_id", "business_memberships", "user_id"),
        ),
        "transaction_items": (
            ("transaction_id", "transactions", "id"),
            ("product_id", "products", "id"),
        ),
        "journal_entries": (
            ("transaction_id", "transactions", "id"),
            ("created_by_user_id", "business_memberships", "user_id"),
        ),
        "journal_lines": (
            ("journal_entry_id", "journal_entries", "id"),
            ("account_id", "accounts", "id"),
        ),
        "stock_batches": (
            ("product_id", "products", "id"),
            ("source_transaction_id", "transactions", "id"),
        ),
        "inventory_movements": (
            ("transaction_id", "transactions", "id"),
            ("product_id", "products", "id"),
        ),
        "payments": (("transaction_id", "transactions", "id"),),
        "expenses": (("transaction_id", "transactions", "id"),),
        "returns": (
            ("original_transaction_id", "transactions", "id"),
            ("return_transaction_id", "transactions", "id"),
            ("product_id", "products", "id"),
        ),
        "stock_adjustments": (
            ("transaction_id", "transactions", "id"),
            ("product_id", "products", "id"),
        ),
        "bank_statements": (("matched_transaction_id", "transactions", "id"),),
        "audit_logs": (
            ("transaction_id", "transactions", "id"),
            ("user_id", "business_memberships", "user_id"),
        ),
        "receipt_attachments": (("transaction_id", "transactions", "id"),),
        "products": (("category_id", "product_categories", "id"),),
    }
    for table, refs in relationships.items():
        if table not in table_names:
            continue
        table_columns = {c["name"] for c in inspect(connection).get_columns(table)}
        for column, parent, parent_column in refs:
            if parent not in table_names:
                continue
            parent_columns = {
                c["name"] for c in inspect(connection).get_columns(parent)
            }
            if not {column, "business_id"}.issubset(table_columns):
                continue
            if not {"business_id", parent_column}.issubset(parent_columns):
                continue
            predicate = (
                f"NEW.{column} IS NOT NULL AND NOT EXISTS "
                f"(SELECT 1 FROM {parent} WHERE {parent}.{parent_column} = "
                f"NEW.{column} AND {parent}.business_id = NEW.business_id)"
            )
            for operation in ("INSERT", "UPDATE"):
                name = f"tenant_{table}_{column}_{operation.lower()}"
                connection.execute(
                    text(
                        f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {operation} "
                        f"ON {table} WHEN {predicate} BEGIN "
                        "SELECT RAISE(ABORT, 'cross-business reference'); END"
                    )
                )


def _install_scoped_unique_indexes(connection) -> None:
    indexes = {
        "business_memberships": ("business_id", "username"),
        "product_categories": ("business_id", "name"),
        "customers": ("business_id", "name"),
        "suppliers": ("business_id", "name"),
        "products": ("business_id", "sku"),
        "accounts": ("business_id", "code"),
        "transactions": ("business_id", "reference"),
        "journal_entries": ("business_id", "transaction_id"),
        "receipt_attachments": ("business_id", "transaction_id"),
    }
    tables = set(inspect(connection).get_table_names())
    for table_name, columns in indexes.items():
        if table_name in tables:
            index_name = f"uq_{table_name}_{'_'.join(columns)}"
            connection.execute(
                text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} "
                    f"ON {table_name} ({', '.join(columns)})"
                )
            )


def _install_tenant_key_indexes(connection) -> None:
    tables = set(inspect(connection).get_table_names())
    for table_name in (
        "business_memberships",
        "product_categories",
        "customers",
        "suppliers",
        "products",
        "accounts",
        "transactions",
        "journal_entries",
        "journal_lines",
        "transaction_items",
        "stock_batches",
        "inventory_movements",
        "payments",
        "expenses",
        "returns",
        "stock_adjustments",
        "bank_statements",
        "audit_logs",
        "receipt_attachments",
    ):
        if table_name in tables and "business_id" in {
            column["name"] for column in inspect(connection).get_columns(table_name)
        }:
            connection.execute(
                text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS "
                    f"uq_{table_name}_business_id_id "
                    f"ON {table_name}(business_id, id)"
                )
            )


def _migrate_old_tables(connection, metadata) -> None:
    from database.models import Base

    _add_missing_columns(connection, metadata)
    _backfill_tenant_columns(connection)
    Base.metadata.create_all(connection)
    _migrate_memberships(connection)
    _install_tenant_key_indexes(connection)
    for table_name in _REBUILD_TABLES:
        _copy_table(
            connection,
            metadata.tables[table_name],
            table_name,
            f"{table_name}_replacement",
        )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_lower "
            "ON users(lower(email)) WHERE email IS NOT NULL AND email <> ''"
        )
    )
    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_business_membership_username_lower "
            "ON business_memberships(business_id, lower(username)) "
            "WHERE username IS NOT NULL AND username <> ''"
        )
    )
    connection.execute(text("DROP INDEX IF EXISTS uq_users_username_lower"))
    connection.execute(text("DROP INDEX IF EXISTS uq_users_email_lower"))
    connection.execute(
        text(
            "CREATE UNIQUE INDEX uq_users_email_lower "
            "ON users(lower(email)) WHERE email IS NOT NULL AND email <> ''"
        )
    )
    _install_tenant_triggers(connection)
    _install_scoped_unique_indexes(connection)


def _migrate_postgresql(connection, metadata) -> None:
    _add_missing_columns(connection, metadata)
    _backfill_tenant_columns(connection)
    metadata.create_all(connection)
    _migrate_memberships(connection)
    inspector = inspect(connection)
    unique_keys = {
        "businesses": ("businesses_name_key",),
        "users": ("users_username_key",),
        "transactions": ("transactions_reference_key",),
    }
    for table_name, columns in (
        ("businesses", ("name",)),
        ("users", ("username",)),
        ("transactions", ("reference",)),
    ):
        existing_constraints = inspector.get_unique_constraints(table_name)
        for constraint in existing_constraints:
            if tuple(constraint.get("column_names") or ()) == columns:
                name = constraint.get("name")
                if name:
                    connection.execute(
                        text(
                            f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS {name}"
                        )
                    )
        for name in unique_keys[table_name]:
            connection.execute(
                text(f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS {name}")
            )
    connection.execute(text("DROP INDEX IF EXISTS uq_users_username_lower"))
    for table_name in (
        "product_categories",
        "customers",
        "suppliers",
        "products",
        "accounts",
        "transactions",
        "journal_entries",
    ):
        connection.execute(
            text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table_name}_business_id_id "
                f"ON {table_name}(business_id, id)"
            )
        )
    _install_tenant_key_indexes(connection)
    _install_postgresql_tenant_constraints(connection)
    _install_scoped_unique_indexes(connection)


def _install_postgresql_tenant_constraints(connection) -> None:
    references = {
        "auth_sessions": (
            ("user_id", "business_id", "business_memberships", "user_id", "business_id"),
        ),
        "role_permissions": (
            ("updated_by_user_id", "business_id", "business_memberships", "user_id", "business_id"),
        ),
        "transactions": (
            ("business_id", "customer_id", "customers", "business_id", "id"),
            ("business_id", "supplier_id", "suppliers", "business_id", "id"),
            ("created_by_user_id", "business_id", "business_memberships", "user_id", "business_id"),
        ),
        "transaction_items": (
            ("business_id", "transaction_id", "transactions", "business_id", "id"),
            ("business_id", "product_id", "products", "business_id", "id"),
        ),
        "journal_entries": (
            ("business_id", "transaction_id", "transactions", "business_id", "id"),
            ("created_by_user_id", "business_id", "business_memberships", "user_id", "business_id"),
        ),
        "journal_lines": (
            ("business_id", "journal_entry_id", "journal_entries", "business_id", "id"),
            ("business_id", "account_id", "accounts", "business_id", "id"),
        ),
        "stock_batches": (
            ("business_id", "product_id", "products", "business_id", "id"),
            ("business_id", "source_transaction_id", "transactions", "business_id", "id"),
        ),
        "inventory_movements": (
            ("business_id", "transaction_id", "transactions", "business_id", "id"),
            ("business_id", "product_id", "products", "business_id", "id"),
        ),
        "payments": (("business_id", "transaction_id", "transactions", "business_id", "id"),),
        "expenses": (("business_id", "transaction_id", "transactions", "business_id", "id"),),
        "returns": (
            ("business_id", "original_transaction_id", "transactions", "business_id", "id"),
            ("business_id", "return_transaction_id", "transactions", "business_id", "id"),
            ("business_id", "product_id", "products", "business_id", "id"),
        ),
        "stock_adjustments": (
            ("business_id", "transaction_id", "transactions", "business_id", "id"),
            ("business_id", "product_id", "products", "business_id", "id"),
        ),
        "bank_statements": (
            ("business_id", "matched_transaction_id", "transactions", "business_id", "id"),
        ),
        "audit_logs": (
            ("business_id", "transaction_id", "transactions", "business_id", "id"),
            ("user_id", "business_id", "business_memberships", "user_id", "business_id"),
        ),
        "receipt_attachments": (
            ("business_id", "transaction_id", "transactions", "business_id", "id"),
        ),
        "products": (
            ("business_id", "category_id", "product_categories", "business_id", "id"),
        ),
    }
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    for table_name, foreign_keys in references.items():
        if table_name not in existing_tables:
            continue
        existing_names = {
            constraint.get("name")
            for constraint in inspector.get_foreign_keys(table_name)
        }
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        for left, right, parent, parent_left, parent_right in foreign_keys:
            if parent not in existing_tables or not {left, right}.issubset(columns):
                continue
            if right not in columns:
                continue
            name = f"fk_{table_name}_{right}_tenant"
            if name in existing_names:
                continue
            connection.execute(
                text(
                    f"ALTER TABLE {table_name} ADD CONSTRAINT {name} "
                    f"FOREIGN KEY ({left}, {right}) "
                    f"REFERENCES {parent} ({parent_left}, {parent_right})"
                )
            )


def upgrade_tenant_schema(connection) -> None:
    from database.backups import create_backup, file_sha256
    from database.models import Base, BackupRecord

    existing_tables = set(inspect(connection).get_table_names())
    backup_path = None
    if (
        "businesses" in existing_tables
        and connection.engine.url.database not in (None, ":memory:")
    ):
        backup_path = create_backup(
            save_history=False, target_engine=connection.engine
        )
    if connection.dialect.name == "sqlite":
        _migrate_old_tables(connection, Base.metadata)
    else:
        _migrate_postgresql(connection, Base.metadata)
    if backup_path is not None:
        connection.execute(
            insert(BackupRecord).values(
                file_name=backup_path.name,
                file_path=str(backup_path),
                backend="sqlite" if connection.dialect.name == "sqlite" else "postgresql",
                size_bytes=backup_path.stat().st_size,
                checksum_sha256=file_sha256(backup_path),
                verified=True,
            )
        )
    if connection.dialect.name == "sqlite":
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"Database migration found foreign-key violations: {violations[:5]}"
            )


def migrate_auth_schema(engine) -> None:
    """Compatibility entry point used by the legacy-auth migration test."""
    with engine.begin() as connection:
        upgrade_tenant_schema(connection)
