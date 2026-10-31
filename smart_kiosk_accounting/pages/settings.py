import os
import tempfile
import subprocess

import streamlit as st
from botocore.exceptions import BotoCoreError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.models import (
    AuditLog,
    BackupRecord,
    Business,
    RoleAssignment,
    RolePermission,
    User,
)
from pages.common import current_business_id, session
from utils.auth_service import (
    create_business_for_user,
    create_member,
    revoke_all_sessions,
)
from utils.authentication import hash_password, verify_password
from utils.authorization import PERMISSIONS, ROLE_DEFAULTS, ROLE_NAMES, user_can
from database.backups import (
    create_backup,
    download_offsite_backup,
    restore_postgresql_backup,
    restore_sqlite_backup,
    verify_backup,
)

PERMISSION_LABELS = {
    "dashboard": "Dashboard",
    "inventory": "Inventory",
    "purchases": "Purchases",
    "sales": "Sales and receipts",
    "expenses": "Expenses",
    "cash_bank": "Cash and bank",
    "customers": "Customers and payments",
    "suppliers": "Suppliers and payments",
    "stock_losses": "Stock adjustments and losses",
    "reports": "Reports and financial statements",
    "journals": "Journals",
    "ledgers": "General ledger",
    "trial_balance": "Trial balance",
    "settings": "Account settings",
    "manage_users": "Manage team accounts",
    "manage_permissions": "Configure role permissions",
    "business_settings": "Business settings",
    "manage_sessions": "Sign out all devices",
}


def render():
    st.title("Settings")
    st.caption("Manage your account, business preferences and team access.")
    db = session()
    try:
        user_id = st.session_state["user_id"]
        business_id = current_business_id()
        user = db.get(User, user_id)
        business = db.get(Business, business_id)
        if user is None or business is None:
            st.error("Your account or business could not be found.")
            return

        st.subheader("Your account")
        st.write(f"Signed in as **{user.full_name or user.username}** · {user.email or 'No email'}")
        if st.button("Sign out from all devices"):
            revoke_all_sessions(db, user.id)
            db.commit()
            st.session_state["force_logout"] = True
            st.rerun()

        with st.form("password_change"):
            st.subheader("Change password")
            current = st.text_input("Current password", type="password")
            new = st.text_input("New password (at least 10 characters)", type="password")
            confirm = st.text_input("Confirm new password", type="password")
            submitted = st.form_submit_button("Change password")
        if submitted:
            if not verify_password(current, user.password_hash):
                st.error("Current password is incorrect.")
            elif len(new) < 10:
                st.error("Use a password with at least 10 characters.")
            elif new != confirm:
                st.error("The new passwords do not match.")
            else:
                user.password_hash = hash_password(new)
                revoke_all_sessions(db, user.id)
                db.commit()
                st.session_state["force_logout"] = True
                st.success("Password updated. Sign in again on this device.")
                st.rerun()

        if user_can(db, user.id, business_id, "business_settings"):
            st.subheader("Business")
            with st.form("business_settings"):
                name = st.text_input("Business name", value=business.name)
                location = st.text_input("Business location", value=business.location)
                method = st.selectbox(
                    "How should stock cost be calculated?",
                    ["FIFO", "WEIGHTED_AVERAGE"],
                    index=["FIFO", "WEIGHTED_AVERAGE"].index(
                        business.inventory_method
                    ),
                    format_func=lambda value: {
                        "FIFO": "FIFO — use the earliest purchase cost first (recommended)",
                        "WEIGHTED_AVERAGE": "Weighted average — blend the cost of all stock",
                    }[value],
                )
                save_business = st.form_submit_button(
                    "Save business settings", type="primary"
                )
            if save_business:
                if not name.strip():
                    st.error("Business name cannot be empty.")
                else:
                    old_values = (
                        f"{business.name}|{business.location}|{business.inventory_method}"
                    )
                    business.name = name.strip()
                    business.location = location.strip()
                    business.inventory_method = method
                    db.add(
                        AuditLog(
                            business_id=business_id,
                            user_id=user.id,
                            action="business_settings_changed",
                            details="Updated business profile and inventory method.",
                            previous_value=old_values,
                            new_value=(
                                f"{business.name}|{business.location}|"
                                f"{business.inventory_method}"
                            ),
                        )
                    )
                    db.commit()
                    st.success("Business settings saved.")

        if user_can(db, user.id, business_id, "business_settings"):
            with st.expander("Add another business to your account"):
                with st.form("add_business"):
                    new_business_name = st.text_input("New business name")
                    new_business_type = st.selectbox(
                        "New business type",
                        [
                            "Retail / shop",
                            "Frozen foods",
                            "Food and restaurant",
                            "Wholesale and distribution",
                            "Fashion and clothing",
                            "Pharmacy and healthcare",
                            "Services",
                            "Other",
                        ],
                    )
                    new_business_location = st.text_input("New business location")
                    new_legal_structure = st.selectbox(
                        "Legal structure",
                        [
                            "Sole proprietorship",
                            "Partnership",
                            "Limited liability company",
                            "Other",
                        ],
                    )
                    add_business = st.form_submit_button("Create another business")
                if add_business:
                    try:
                        added_business = create_business_for_user(
                            db,
                            user,
                            name=new_business_name,
                            business_type=new_business_type,
                            location=new_business_location,
                            legal_structure=new_legal_structure,
                        )
                        db.commit()
                        st.success(
                            f"{added_business.name} was created. Select it from "
                            "the Active business menu to switch."
                        )
                    except (ValueError, IntegrityError, PermissionError) as exc:
                        db.rollback()
                        st.error(str(exc))

        if user_can(db, user.id, business_id, "manage_users"):
            st.subheader("Team accounts")
            with st.form("add_team_member"):
                full_name = st.text_input("Team member full name")
                email = st.text_input("Team member email")
                phone = st.text_input("Team member phone")
                username = st.text_input("Team member username")
                temporary_password = st.text_input(
                    "Initial password (at least 10 characters)", type="password"
                )
                role = st.selectbox(
                    "Role", ["MANAGER", "CASHIER", "ACCOUNTANT", "STOCK_MANAGER"]
                )
                add_member = st.form_submit_button("Create team account")
            if add_member:
                try:
                    create_member(
                        db,
                        business_id=business_id,
                        full_name=full_name,
                        email=email,
                        phone=phone,
                        username=username,
                        password=temporary_password,
                        role=role,
                    )
                    db.commit()
                    st.success("Team account created. Share its credentials securely.")
                except (ValueError, IntegrityError) as exc:
                    db.rollback()
                    st.error(
                        "Those account details may already be in use."
                        if isinstance(exc, IntegrityError) else str(exc)
                    )

            assignments = list(
                db.scalars(
                    select(RoleAssignment).where(
                        RoleAssignment.business_id == business_id
                    ).order_by(RoleAssignment.id)
                )
            )
            for assignment in assignments:
                member = db.get(User, assignment.user_id)
                if member is None or member.id == user.id:
                    continue
                with st.form(f"team_member_{member.id}"):
                    st.write(f"**{member.full_name or member.username}** · {member.email}")
                    new_role = st.selectbox(
                        "Role",
                        ["MANAGER", "CASHIER", "ACCOUNTANT", "STOCK_MANAGER"],
                        index=(
                            ["MANAGER", "CASHIER", "ACCOUNTANT", "STOCK_MANAGER"]
                            .index(assignment.role)
                        ),
                        key=f"role_{member.id}",
                    )
                    active = st.checkbox(
                        "Account active", value=assignment.is_active,
                        key=f"active_{member.id}"
                    )
                    update_member = st.form_submit_button("Update account")
                if update_member:
                    previous_role = assignment.role
                    previous_active = assignment.is_active
                    assignment.role = new_role
                    assignment.is_active = active
                    db.add(
                        AuditLog(
                            business_id=business_id,
                            user_id=user.id,
                            action="member_permissions_changed",
                            details=f"Updated {member.username} membership.",
                            previous_value=(
                                f"role={previous_role};active={previous_active}"
                            ),
                            new_value=f"role={new_role};active={active}",
                        )
                    )
                    db.commit()
                    st.success("Team account updated.")

        if user_can(db, user.id, business_id, "manage_permissions"):
            st.subheader("Role permissions")
            st.caption(
                "Choose which application areas each team role can access. "
                "Owners always retain full access."
            )
            for role_name in ROLE_NAMES:
                if role_name == "OWNER":
                    continue
                grants = {
                    item.permission: item
                    for item in db.scalars(
                        select(RolePermission).where(
                            RolePermission.business_id == business_id,
                            RolePermission.role == role_name,
                        )
                    )
                }
                with st.form(f"permissions_{role_name}"):
                    st.markdown(f"**{role_name.replace('_', ' ').title()}**")
                    selected = {
                        permission: st.checkbox(
                            PERMISSION_LABELS.get(permission, permission),
                            value=(
                                grants[permission].allowed
                                if permission in grants
                                else permission in ROLE_DEFAULTS[role_name]
                            ),
                            key=f"{role_name}_{permission}",
                        )
                        for permission in PERMISSIONS
                    }
                    save_permissions = st.form_submit_button("Save permissions")
                if save_permissions:
                    for permission, allowed in selected.items():
                        grant = grants.get(permission)
                        old_allowed = (
                            grant.allowed if grant is not None
                            else permission in ROLE_DEFAULTS[role_name]
                        )
                        if grant is None:
                            db.add(
                                RolePermission(
                                    business_id=business_id,
                                    role=role_name,
                                    permission=permission,
                                    allowed=allowed,
                                    updated_by_user_id=user.id,
                                )
                            )
                        else:
                            grant.allowed = allowed
                            grant.updated_by_user_id = user.id
                        db.add(
                            AuditLog(
                                business_id=business_id,
                                user_id=user.id,
                                action="role_permission_changed",
                                details=f"{role_name}: {permission}",
                                previous_value=str(old_allowed),
                                new_value=str(allowed),
                            )
                        )
                    db.commit()
                    st.success(f"{role_name.replace('_', ' ').title()} permissions saved.")

        backup_admin_ids = {
            int(value.strip())
            for value in os.getenv("SKA_BACKUP_ADMIN_USER_IDS", "").split(",")
            if value.strip().isdigit()
        }
        if user.id in backup_admin_ids:
            st.subheader("Database backups")
            st.caption(
                "Application-wide database backups are restricted to configured "
                "platform administrators."
            )
            if st.button("Create and verify backup"):
                try:
                    offsite = bool(os.getenv("SKA_BACKUP_S3_BUCKET", "").strip())
                    record = create_backup(upload_offsite=offsite)
                    st.success(
                        f"Verified backup created: {record.file_name}"
                        + (" and uploaded off-site." if offsite else ".")
                    )
                except (
                    BotoCoreError,
                    OSError,
                    RuntimeError,
                    ValueError,
                    subprocess.CalledProcessError,
                ) as exc:
                    st.error(f"Backup failed: {exc}")

            backup_rows = list(
                db.scalars(
                    select(BackupRecord).order_by(BackupRecord.created_at.desc())
                )
            )
            for backup in backup_rows:
                st.write(
                    f"{backup.created_at:%Y-%m-%d %H:%M} · {backup.file_name} · "
                    f"{backup.size_bytes:,} bytes · "
                    f"{'verified' if backup.verified else 'unverified'}"
                )
                backup_path = os.path.abspath(backup.file_path)
                if backup.verified and os.path.isfile(backup_path):
                    with open(backup_path, "rb") as file:
                        st.download_button(
                            f"Download {backup.file_name}",
                            data=file.read(),
                            file_name=backup.file_name,
                            key=f"download_backup_{backup.id}",
                        )
                elif backup.offsite:
                    if st.button(
                        f"Retrieve {backup.file_name} from off-site storage",
                        key=f"retrieve_backup_{backup.id}",
                    ):
                        try:
                            retrieved_path = download_offsite_backup(backup)
                            st.download_button(
                                f"Download {backup.file_name}",
                                data=retrieved_path.read_bytes(),
                                file_name=backup.file_name,
                                key=f"download_retrieved_backup_{backup.id}",
                            )
                        except (
                            BotoCoreError,
                            OSError,
                            RuntimeError,
                            ValueError,
                        ) as exc:
                            st.error(f"Could not retrieve backup: {exc}")

            uploaded_backup = st.file_uploader(
                "Restore a verified SQLite or PostgreSQL backup",
                type=["sqlite3", "db", "dump"],
                help=(
                    "Restoring replaces the current database after creating a "
                    "safety backup. PostgreSQL dumps require pg_restore."
                ),
            )
            confirm_restore = st.checkbox(
                "I understand this replaces the current database.",
                key="confirm_database_restore",
            )
            if st.button(
                "Restore uploaded backup",
                disabled=uploaded_backup is None or not confirm_restore,
            ):
                temporary_path = None
                try:
                    with tempfile.NamedTemporaryFile(
                        suffix=(
                            ".dump"
                            if uploaded_backup.name.lower().endswith(".dump")
                            else ".sqlite3"
                        ),
                        delete=False,
                    ) as temporary:
                        temporary.write(uploaded_backup.getvalue())
                        temporary_path = temporary.name
                    db.close()
                    if temporary_path.endswith(".dump"):
                        restore_postgresql_backup(temporary_path, confirmed=True)
                    else:
                        verify_backup(temporary_path, "sqlite")
                        restore_sqlite_backup(temporary_path)
                    st.success("Database restored successfully.")
                    st.rerun()
                except (
                    BotoCoreError,
                    OSError,
                    RuntimeError,
                    ValueError,
                    subprocess.CalledProcessError,
                ) as exc:
                    st.error(f"Database restore failed: {exc}")
                finally:
                    if temporary_path and os.path.exists(temporary_path):
                        os.unlink(temporary_path)
    finally:
        db.close()
