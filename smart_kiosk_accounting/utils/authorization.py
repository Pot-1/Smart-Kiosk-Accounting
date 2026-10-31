from sqlalchemy import select

from database.models import RoleAssignment, RolePermission

ROLE_DEFAULTS = {
    "OWNER": {
        "dashboard", "inventory", "purchases", "sales", "expenses", "cash_bank",
        "customers", "suppliers", "stock_losses", "reports", "journals",
        "ledgers", "trial_balance", "settings", "manage_users",
        "manage_permissions", "business_settings", "manage_sessions",
    },
    "MANAGER": {
        "dashboard", "inventory", "purchases", "sales", "expenses", "cash_bank",
        "customers", "suppliers", "stock_losses", "reports", "settings",
    },
    "CASHIER": {"dashboard", "sales", "customers", "settings"},
    "ACCOUNTANT": {
        "dashboard", "cash_bank", "reports", "journals", "ledgers",
        "trial_balance", "settings",
    },
    "STOCK_MANAGER": {
        "dashboard", "inventory", "purchases", "suppliers", "stock_losses",
        "reports", "settings",
    },
}

ROLE_NAMES = tuple(ROLE_DEFAULTS)
PERMISSIONS = tuple(sorted(set().union(*ROLE_DEFAULTS.values())))


def seed_role_permissions(session, business_id: int) -> None:
    existing = {
        (row.role, row.permission)
        for row in session.scalars(
            select(RolePermission).where(RolePermission.business_id == business_id)
        )
    }
    session.add_all(
        RolePermission(
            business_id=business_id,
            role=role,
            permission=permission,
            allowed=permission in ROLE_DEFAULTS[role],
        )
        for role in ROLE_NAMES
        for permission in PERMISSIONS
        if (role, permission) not in existing
    )


def user_role(session, user_id: int, business_id: int) -> str | None:
    assignment = session.scalar(
        select(RoleAssignment).where(
            RoleAssignment.user_id == user_id,
            RoleAssignment.business_id == business_id,
            RoleAssignment.is_active.is_(True),
        )
    )
    return assignment.role if assignment else None


def user_can(session, user_id: int, business_id: int, permission: str) -> bool:
    from database.tenant import tenant_system_operation

    with tenant_system_operation(session):
        role = user_role(session, user_id, business_id)
        if role is None:
            return False
        if role == "OWNER":
            return True
        grant = session.scalar(
            select(RolePermission).where(
                RolePermission.business_id == business_id,
                RolePermission.role == role,
                RolePermission.permission == permission,
            )
        )
        return bool(grant and grant.allowed)
