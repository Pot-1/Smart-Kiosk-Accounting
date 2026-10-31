import hashlib
import secrets
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database.migrations import migrate_auth_schema
from database.seed_data import seed_demo_business
from database.tenant import require_business_access
from database.models import (
    AuthEvent,
    Account,
    Base,
    Business,
    JournalEntry,
    JournalLine,
    PasswordResetToken,
    RoleAssignment,
    RolePermission,
    Transaction,
    TransactionItem,
    User,
)
from tests.support import DatabaseTestCase
from utils.auth_service import (
    AuthenticationError,
    authenticate_user,
    create_auth_session,
    create_member,
    create_password_reset,
    register_user,
    reset_password,
    resolve_auth_session,
    revoke_all_sessions,
    revoke_auth_session,
)
from utils.authentication import verify_password
from utils.authorization import seed_role_permissions, user_can
from utils.helpers import lagos_now


REGISTRATION = {
    "full_name": "Ada Okafor",
    "email": "Ada@example.com",
    "phone": "08012345678",
    "username": "ada.okafor",
    "password": "StrongPass!234",
    "business_name": "Ada Foods",
    "business_type": "Retail / shop",
    "location": "Lagos",
    "legal_structure": "Sole proprietorship",
}


class AuthenticationTests(DatabaseTestCase):
    def create_account(self, **overrides):
        values = {**REGISTRATION, **overrides}
        user, business = register_user(self.db, **values)
        self.db.commit()
        self.db.info.update(
            tenant_user_id=user.id,
            tenant_business_id=business.id,
        )
        return user, business

    def test_successful_registration_creates_business_and_owner_role(self):
        user, business = self.create_account()

        self.assertEqual(user.email, "ada@example.com")
        self.assertEqual(user.phone, "+2348012345678")
        self.assertEqual(business.name, "Ada Foods")
        self.assertFalse(business.onboarding_complete)
        self.assertEqual(
            self.db.scalar(
                select(RoleAssignment.role).where(RoleAssignment.user_id == user.id)
            ),
            "OWNER",
        )
        self.assertTrue(user_can(self.db, user.id, business.id, "manage_users"))
        self.assertTrue(verify_password(REGISTRATION["password"], user.password_hash))

    def test_demo_seed_requires_explicit_demo_mode(self):
        with patch("database.seed_data.DEMO_MODE", False):
            with self.assertRaisesRegex(RuntimeError, "SKA_DEMO_MODE"):
                seed_demo_business(self.db)

    def test_duplicate_email_is_case_insensitive(self):
        self.create_account()
        with self.assertRaisesRegex(ValueError, "email address"):
            register_user(
                self.db,
                **{**REGISTRATION, "email": "ADA@EXAMPLE.COM", "username": "ada2"},
            )

    def test_username_is_unique_within_business_not_globally(self):
        _, first_business = self.create_account()
        user, business = register_user(
            self.db,
            **{
                **REGISTRATION,
                "email": "another@example.com",
                "business_name": "Another Shop",
                "username": "ADA.OKAFOR",
            },
        )
        self.assertEqual(user.username, "ada.okafor")
        self.assertNotEqual(first_business.id, business.id)

    def test_incorrect_password_is_rejected_and_logged(self):
        user, _ = self.create_account()
        with self.assertRaises(AuthenticationError):
            authenticate_user(self.db, user.email, "incorrect password")
        self.db.commit()
        self.assertEqual(user.failed_login_attempts, 1)
        self.assertEqual(
            self.db.scalar(
                select(AuthEvent.event_type).where(AuthEvent.user_id == user.id)
            ),
            "login_failed",
        )

    def test_password_reset_is_single_use_and_revokes_sessions(self):
        user, business = self.create_account()
        raw_session, _ = create_auth_session(self.db, user, remember_device=True)
        raw_reset = create_password_reset(self.db, user.email)
        self.db.commit()

        reset_password(self.db, raw_reset, "NewSecurePass!234")
        self.db.commit()
        self.assertTrue(verify_password("NewSecurePass!234", user.password_hash))
        self.assertIsNone(resolve_auth_session(self.db, raw_session))
        with self.assertRaisesRegex(ValueError, "invalid or has expired"):
            reset_password(self.db, raw_reset, "AnotherSecurePass!234")
        self.assertEqual(user.business_id, business.id)

    def test_expired_password_reset_token_is_rejected(self):
        user, _ = self.create_account()
        raw_token = create_password_reset(self.db, user.email)
        token = self.db.scalar(
            select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
        )
        token.expires_at = lagos_now() - timedelta(seconds=1)
        self.db.commit()

        with self.assertRaisesRegex(ValueError, "invalid or has expired"):
            reset_password(self.db, raw_token, "AnotherSecurePass!234")

    def test_locked_account_rejects_more_login_attempts(self):
        user, _ = self.create_account()
        for _ in range(5):
            with self.assertRaises(AuthenticationError):
                authenticate_user(self.db, user.username, "wrong password")
            self.db.commit()
        self.assertIsNotNone(user.locked_until)
        with self.assertRaisesRegex(AuthenticationError, "Too many"):
            authenticate_user(self.db, user.username, REGISTRATION["password"])

    def test_password_reset_clears_temporary_login_lock(self):
        user, _ = self.create_account()
        for _ in range(5):
            with self.assertRaises(AuthenticationError):
                authenticate_user(self.db, user.username, "wrong password")
            self.db.commit()
        token = create_password_reset(self.db, user.email)
        self.db.commit()
        reset_password(self.db, token, "RecoveryPass!234")
        self.db.commit()

        authenticated, _, _ = authenticate_user(
            self.db, user.username, "RecoveryPass!234"
        )
        self.assertEqual(authenticated.id, user.id)

    def test_expired_session_is_rejected(self):
        user, _ = self.create_account()
        raw_token, auth_session = create_auth_session(self.db, user, False)
        self.db.commit()
        auth_session.expires_at = lagos_now() - timedelta(seconds=1)
        self.db.commit()

        self.assertIsNone(resolve_auth_session(self.db, raw_token))

    def test_user_can_sign_out_from_current_and_all_sessions(self):
        user, business = self.create_account()
        first_token, _ = create_auth_session(self.db, user, False)
        second_token, _ = create_auth_session(self.db, user, True)
        self.db.commit()

        revoke_auth_session(self.db, first_token)
        self.db.commit()
        self.assertIsNone(resolve_auth_session(self.db, first_token))
        self.assertIsNotNone(resolve_auth_session(self.db, second_token))
        revoke_all_sessions(self.db, user.id)
        self.db.commit()
        self.assertIsNone(resolve_auth_session(self.db, second_token))
        self.assertEqual(
            self.db.scalar(
                select(RoleAssignment.business_id).where(
                    RoleAssignment.user_id == user.id
                )
            ),
            business.id,
        )

    def test_role_permissions_are_configurable(self):
        user, business = self.create_account()
        member = create_member(
            self.db,
            business_id=business.id,
            full_name="Cashier User",
            email="cashier@example.com",
            phone="08012345678",
            username="cashier1",
            password="StrongPass!234",
            role="CASHIER",
        )
        seed_role_permissions(self.db, business.id)
        self.db.flush()
        self.assertTrue(user_can(self.db, member.id, business.id, "sales"))
        grant = self.db.scalar(
            select(RolePermission).where(
                RolePermission.business_id == business.id,
                RolePermission.role == "CASHIER",
                RolePermission.permission == "sales",
            )
        )
        grant.allowed = False
        self.db.flush()
        self.assertFalse(user_can(self.db, member.id, business.id, "sales"))
        self.assertTrue(user_can(self.db, user.id, business.id, "sales"))

    def test_cashier_cannot_access_business_setup_permissions(self):
        _, business = self.create_account()
        member = create_member(
            self.db,
            business_id=business.id,
            full_name="Cashier User",
            email="cashier-setup@example.com",
            phone="08012345678",
            username="cashier-setup",
            password="CashierPass!234",
            role="CASHIER",
        )
        self.db.commit()
        self.db.info.update(
            tenant_user_id=member.id,
            tenant_business_id=business.id,
        )

        with self.assertRaisesRegex(PermissionError, "role does not allow"):
            require_business_access(self.db, business.id, "business_settings")

    def test_existing_admin_hash_and_business_migrate_to_owner(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", b"legacy-password", salt, 310_000
        ).hex()
        legacy_hash = f"{salt.hex()}:{digest}"
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE businesses (id INTEGER PRIMARY KEY, name VARCHAR(160) "
                    "NOT NULL UNIQUE, currency VARCHAR(3), timezone VARCHAR(40), "
                    "inventory_method VARCHAR(20), created_at DATETIME)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE users (id INTEGER PRIMARY KEY, business_id INTEGER "
                    "NOT NULL REFERENCES businesses(id), username VARCHAR(80) UNIQUE, "
                    "password_hash VARCHAR(256) NOT NULL, is_active BOOLEAN)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO businesses (id,name,currency,timezone,inventory_method) "
                    "VALUES (1,'Legacy Shop','NGN','Africa/Lagos','FIFO')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO users (id,business_id,username,password_hash,is_active) "
                    "VALUES (1,1,'admin',:password_hash,1)"
                ),
                {"password_hash": legacy_hash},
            )
            connection.execute(
                text(
                    "CREATE TABLE accounts (id INTEGER PRIMARY KEY, business_id "
                    "INTEGER NOT NULL, code VARCHAR(12), name VARCHAR(120), "
                    "account_type VARCHAR(20))"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE transactions (id INTEGER PRIMARY KEY, business_id "
                    "INTEGER NOT NULL, customer_id INTEGER, supplier_id INTEGER, "
                    "reference VARCHAR(40) UNIQUE, transaction_type VARCHAR(30), "
                    "transaction_date DATETIME, counterparty_name VARCHAR(160), "
                    "payment_method VARCHAR(20), total_kobo INTEGER, memo TEXT, "
                    "status VARCHAR(20))"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE transaction_items (id INTEGER PRIMARY KEY, "
                    "transaction_id INTEGER, product_id INTEGER, description "
                    "VARCHAR(200), quantity INTEGER, unit_price_kobo INTEGER, "
                    "total_kobo INTEGER, cost_kobo INTEGER)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE journal_entries (id INTEGER PRIMARY KEY, "
                    "business_id INTEGER, transaction_id INTEGER UNIQUE, "
                    "entry_date DATETIME, memo TEXT)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE journal_lines (id INTEGER PRIMARY KEY, "
                    "journal_entry_id INTEGER, account_id INTEGER, debit_kobo "
                    "INTEGER, credit_kobo INTEGER)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO accounts VALUES (1,1,'1010','Cash','asset')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO transactions VALUES "
                    "(1,1,NULL,NULL,'LEGACY-1','sale','2025-01-01',"
                    "'Legacy customer','cash',12500,'Legacy sale','posted')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO transactions VALUES "
                    "(2,1,NULL,NULL,'LEGACY-2','sale','2025-01-02',"
                    "'Legacy credit customer','credit',8000,'Legacy credit sale','posted')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO transaction_items VALUES "
                    "(1,1,NULL,'Legacy item',1,12500,12500,0)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO journal_entries VALUES "
                    "(1,1,1,'2025-01-01','Legacy journal')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO journal_lines VALUES "
                    "(1,1,1,12500,0),(2,1,1,0,12500)"
                )
            )

        Base.metadata.create_all(engine)
        migrate_auth_schema(engine)
        with Session(engine) as migrated:
            migrated.info["system_operation"] = True
            user = migrated.get(User, 1)
            business = migrated.get(Business, 1)
            self.assertTrue(verify_password("legacy-password", user.password_hash))
            self.assertEqual(
                migrated.scalar(
                    select(RoleAssignment.role).where(
                        RoleAssignment.user_id == user.id
                    )
                ),
                "OWNER",
            )
            self.assertTrue(user_can(migrated, user.id, business.id, "manage_users"))
            self.assertTrue(business.onboarding_complete)
            self.assertEqual(migrated.get(Transaction, 1).total_kobo, 12500)
            self.assertEqual(
                migrated.get(Transaction, 2).credit_amount_kobo, 8000
            )
            self.assertEqual(
                migrated.scalar(
                    select(TransactionItem.business_id).where(
                        TransactionItem.id == 1
                    )
                ),
                business.id,
            )
            self.assertEqual(
                migrated.scalar(
                    select(JournalLine.business_id).where(JournalLine.id == 1)
                ),
                business.id,
            )
        engine.dispose()

    def test_legacy_password_hash_upgrades_after_successful_login(self):
        user, _ = self.create_account()
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", b"legacy-password", salt, 310_000
        ).hex()
        user.password_hash = f"{salt.hex()}:{digest}"
        self.db.commit()

        authenticated, _, _ = authenticate_user(
            self.db, user.username, "legacy-password"
        )
        self.assertTrue(authenticated.password_hash.startswith("pbkdf2_sha256$"))


if __name__ == "__main__":
    unittest.main()
