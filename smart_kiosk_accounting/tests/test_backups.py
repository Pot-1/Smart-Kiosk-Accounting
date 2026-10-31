import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy import text

from database.backups import create_backup, file_sha256, verify_backup
from database.migrations import upgrade_tenant_schema


class BackupTests(unittest.TestCase):
    def test_sqlite_backup_is_independently_readable_and_verified(self):
        with tempfile.TemporaryDirectory(prefix="ska-backup-test-") as directory:
            root = Path(directory)
            source_path = root / "source.sqlite3"
            source = sqlite3.connect(str(source_path))
            source.execute(
                "CREATE TABLE financial_records "
                "(id INTEGER PRIMARY KEY, amount_kobo INTEGER NOT NULL)"
            )
            source.execute(
                "INSERT INTO financial_records (amount_kobo) VALUES (12345)"
            )
            source.commit()
            source.close()
            source_engine = create_engine(f"sqlite:///{source_path}")
            try:
                with patch.dict(os.environ, {"SKA_BACKUP_DIR": str(root / "backups")}):
                    backup = create_backup(
                        save_history=False, target_engine=source_engine
                    )
                self.assertTrue(verify_backup(backup, "sqlite"))
                self.assertEqual(len(file_sha256(backup)), 64)
                restored = sqlite3.connect(str(backup))
                try:
                    self.assertEqual(
                        restored.execute(
                            "SELECT amount_kobo FROM financial_records"
                        ).fetchone(),
                        (12345,),
                    )
                finally:
                    restored.close()
            finally:
                source_engine.dispose()

    def test_invalid_sqlite_backup_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="ska-backup-invalid-") as directory:
            invalid = Path(directory) / "invalid.sqlite3"
            invalid.write_bytes(b"not a database")
            with self.assertRaisesRegex(ValueError, "integrity"):
                verify_backup(invalid, "sqlite")

    def test_existing_database_is_backed_up_before_migration(self):
        with tempfile.TemporaryDirectory(prefix="ska-migration-backup-") as directory:
            root = Path(directory)
            source_path = root / "legacy.sqlite3"
            source = sqlite3.connect(str(source_path))
            source.execute(
                "CREATE TABLE businesses (id INTEGER PRIMARY KEY, "
                "name VARCHAR(160) NOT NULL UNIQUE, currency VARCHAR(3), "
                "timezone VARCHAR(40), inventory_method VARCHAR(20), "
                "created_at DATETIME)"
            )
            source.execute(
                "INSERT INTO businesses (id, name, currency, timezone, "
                "inventory_method, created_at) VALUES "
                "(1, 'Legacy business', 'NGN', 'Africa/Lagos', 'FIFO', NULL)"
            )
            source.commit()
            source.close()
            migration_engine = create_engine(f"sqlite:///{source_path}")
            try:
                with patch.dict(
                    os.environ, {"SKA_BACKUP_DIR": str(root / "backups")}
                ):
                    with migration_engine.connect() as connection:
                        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                        connection.commit()
                        with connection.begin():
                            upgrade_tenant_schema(connection)
                            backup = connection.scalar(
                                text("SELECT file_path FROM backup_records")
                            )
                self.assertIsNotNone(backup)
                self.assertTrue(verify_backup(backup, "sqlite"))
                restored = sqlite3.connect(str(source_path))
                try:
                    self.assertEqual(
                        restored.execute(
                            "SELECT name FROM businesses WHERE id=1"
                        ).fetchone(),
                        ("Legacy business",),
                    )
                finally:
                    restored.close()
            finally:
                migration_engine.dispose()


if __name__ == "__main__":
    unittest.main()
