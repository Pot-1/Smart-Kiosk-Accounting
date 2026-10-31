import hashlib
import re
import secrets
import smtplib
from functools import wraps
from datetime import timedelta
from email.message import EmailMessage

from sqlalchemy import func, select

from accounting.chart_of_accounts import DEFAULT_ACCOUNTS
from config import (
    PASSWORD_RESET_MINUTES,
    REMEMBERED_SESSION_DAYS,
    SESSION_LIFETIME_HOURS,
)
from database.models import (
    Account,
    AuditLog,
    AuthEvent,
    AuthSession,
    Business,
    PasswordResetToken,
    RoleAssignment,
    User,
)
from database.tenant import require_business_access, tenant_system_operation
from utils.authentication import (
    hash_password,
    password_hash_needs_upgrade,
    verify_password,
)
from utils.authorization import seed_role_permissions
from utils.helpers import lagos_now

LOGIN_FAILURE_LIMIT = 5
LOGIN_LOCK_MINUTES = 15
RESET_REQUEST_LIMIT = 3
RESET_REQUEST_WINDOW_MINUTES = 60
BUSINESS_TYPES = {
    "Retail / shop", "Frozen foods", "Food and restaurant",
    "Wholesale and distribution", "Fashion and clothing",
    "Pharmacy and healthcare", "Services", "Other",
}
LEGAL_STRUCTURES = {
    "Sole proprietorship", "Partnership",
    "Limited liability company", "Other",
}
_DUMMY_PASSWORD_HASH = hash_password("not-a-real-user-password")


class AuthenticationError(ValueError):
    pass


def normalize_email(email: str) -> str:
    value = email.strip().casefold()
    if len(value) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise ValueError("Enter a valid email address.")
    return value


def normalize_phone(phone: str) -> str:
    value = re.sub(r"[\s().-]", "", phone.strip())
    if value.startswith("0") and len(value) == 11:
        value = "+234" + value[1:]
    elif value.startswith("234") and len(value) == 13:
        value = "+" + value
    if not re.fullmatch(r"\+[1-9]\d{7,14}", value):
        raise ValueError("Enter a valid phone number, including the country code.")
    return value


def validate_password(password: str) -> None:
    if len(password) < 10:
        raise ValueError("Use a password with at least 10 characters.")
    if len(password) > 1024:
        raise ValueError("Password must be 1024 characters or fewer.")


def _validate_username(username: str) -> str:
    value = username.strip().casefold()
    if not re.fullmatch(r"[a-z0-9._-]{3,80}", value):
        raise ValueError(
            "Username must be 3–80 characters and use letters, numbers, dots, "
            "underscores or hyphens."
        )
    return value


def _identifier_hash(identifier: str) -> str:
    return hashlib.sha256(identifier.strip().casefold().encode()).hexdigest()


def _identity_operation(function):
    @wraps(function)
    def wrapped(session, *args, **kwargs):
        with tenant_system_operation(session):
            return function(session, *args, **kwargs)

    return wrapped


def register_user(
    session,
    *,
    full_name: str,
    email: str,
    phone: str,
    username: str,
    password: str,
    business_name: str,
    business_type: str,
    location: str,
    legal_structure: str,
):
    with tenant_system_operation(session):
        return _register_user(
            session,
            full_name=full_name,
            email=email,
            phone=phone,
            username=username,
            password=password,
            business_name=business_name,
            business_type=business_type,
            location=location,
            legal_structure=legal_structure,
        )


def _register_user(
    session,
    *,
    full_name: str,
    email: str,
    phone: str,
    username: str,
    password: str,
    business_name: str,
    business_type: str,
    location: str,
    legal_structure: str,
):
    full_name = full_name.strip()
    business_name = business_name.strip()
    location = location.strip()
    if not full_name:
        raise ValueError("Enter your full name.")
    if not business_name:
        raise ValueError("Enter your business name.")
    if len(full_name) > 160 or len(business_name) > 160 or len(location) > 200:
        raise ValueError("One or more details are too long.")
    if business_type not in BUSINESS_TYPES:
        raise ValueError("Choose a valid business type.")
    if legal_structure not in LEGAL_STRUCTURES:
        raise ValueError("Choose a valid legal structure.")
    normalized_email = normalize_email(email)
    normalized_phone = normalize_phone(phone)
    normalized_username = _validate_username(username)
    validate_password(password)
    if session.scalar(
        select(User.id).where(func.lower(User.email) == normalized_email)
    ):
        raise ValueError("An account already uses that email address.")

    business = Business(
        name=business_name,
        business_type=business_type.strip()[:80],
        location=location,
        legal_structure=legal_structure.strip()[:60],
        onboarding_step=1,
        onboarding_complete=False,
    )
    session.add(business)
    session.flush()
    session.add_all(
        Account(
            business_id=business.id, code=code, name=name, account_type=kind
        )
        for code, name, kind in DEFAULT_ACCOUNTS
    )
    user = User(
        business_id=business.id,
        username=normalized_username,
        password_hash=hash_password(password),
        full_name=full_name,
        email=normalized_email,
        phone=normalized_phone,
    )
    session.add(user)
    session.flush()
    business.created_by_user_id = user.id
    session.add(
        RoleAssignment(
            user_id=user.id,
            business_id=business.id,
            username=normalized_username,
            role="OWNER",
        )
    )
    seed_role_permissions(session, business.id)
    session.flush()
    session.add(
        AuditLog(
            business_id=business.id,
            user_id=user.id,
            action="business_created",
            details=f"Registered business {business.name}.",
            new_value=business.name,
        )
    )
    session.flush()
    return user, business


def create_member(
    session, *, business_id: int, full_name: str, email: str, phone: str,
    username: str, password: str, role: str,
):
    require_business_access(session, business_id, "manage_users")
    if not full_name.strip():
        raise ValueError("Enter the team member's full name.")
    role = role.strip().upper()
    if role not in {"MANAGER", "CASHIER", "ACCOUNTANT", "STOCK_MANAGER"}:
        raise ValueError("Choose a valid non-owner role.")
    normalized_email = normalize_email(email)
    normalized_phone = normalize_phone(phone)
    normalized_username = _validate_username(username)
    validate_password(password)
    if session.scalar(
        select(RoleAssignment.id).where(
            RoleAssignment.business_id == business_id,
            func.lower(RoleAssignment.username) == normalized_username,
        )
    ):
        raise ValueError("That username is already in use in this business.")
    business = session.get(Business, business_id)
    if business is None:
        raise ValueError("Business not found.")
    user = session.scalar(
        select(User)
        .where(func.lower(User.email) == normalized_email)
        .execution_options(tenant_user_lookup=True)
    )
    if user is None:
        user = User(
            business_id=business_id,
            username=normalized_username,
            password_hash=hash_password(password),
            full_name=full_name.strip(),
            email=normalized_email,
            phone=normalized_phone,
        )
        session.add(user)
        session.flush()
    elif session.scalar(
        select(RoleAssignment.id).where(
            RoleAssignment.user_id == user.id,
            RoleAssignment.business_id == business_id,
        )
    ):
        raise ValueError("That user is already a member of this business.")
    session.add(
        RoleAssignment(
            user_id=user.id, business_id=business_id,
            username=normalized_username, role=role,
        )
    )
    session.add(
        AuditLog(
            business_id=business_id,
            user_id=session.info["tenant_user_id"],
            action="member_added",
            details=f"Added {user.email or user.username} as {role}.",
            new_value=role,
        )
    )
    return user


def create_business_for_user(
    session, user: User, *, name: str, business_type: str = "Other",
    location: str = "", legal_structure: str = "Other",
):
    active_business_id = session.info.get("tenant_business_id")
    if user.id != session.info.get("tenant_user_id"):
        raise PermissionError("You can only add a business to your own account.")
    if active_business_id is None:
        raise PermissionError("An authenticated business membership is required.")
    require_business_access(session, active_business_id, "business_settings")
    name = name.strip()
    if not name:
        raise ValueError("Enter a business name.")
    if len(name) > 160 or len(location) > 200:
        raise ValueError("Business details are too long.")
    if business_type not in BUSINESS_TYPES or legal_structure not in LEGAL_STRUCTURES:
        raise ValueError("Choose valid business details.")
    with tenant_system_operation(session):
        business = Business(
            name=name,
            business_type=business_type,
            location=location.strip(),
            legal_structure=legal_structure,
            onboarding_step=1,
            onboarding_complete=False,
            created_by_user_id=user.id,
        )
        session.add(business)
        session.flush()
        session.add_all(
            Account(
                business_id=business.id, code=code, name=account_name,
                account_type=kind,
            )
            for code, account_name, kind in DEFAULT_ACCOUNTS
        )
        session.add(
            RoleAssignment(
                user_id=user.id,
                business_id=business.id,
                username=_validate_username(user.username),
                role="OWNER",
            )
        )
        seed_role_permissions(session, business.id)
        session.flush()
        session.add(
            AuditLog(
                business_id=business.id,
                user_id=user.id,
                action="business_created",
                details=f"Created business {business.name}.",
                new_value=business.name,
            )
        )
    return business


@_identity_operation
def authenticate_user(session, identifier: str, password: str):
    normalized = identifier.strip().casefold()
    if not normalized:
        raise AuthenticationError("Enter your username or email and password.")
    identifier_hash = _identifier_hash(normalized)
    now = lagos_now()
    last_counter_reset = session.scalar(
        select(func.max(AuthEvent.created_at)).where(
            AuthEvent.identifier_hash == identifier_hash,
            AuthEvent.event_type.in_(("login_succeeded", "login_counter_reset")),
        )
    )
    failure_filters = [
        AuthEvent.identifier_hash == identifier_hash,
        AuthEvent.event_type == "login_failed",
        AuthEvent.created_at >= now - timedelta(minutes=LOGIN_LOCK_MINUTES),
    ]
    if last_counter_reset is not None:
        failure_filters.append(AuthEvent.created_at > last_counter_reset)
    recent_failures = session.scalar(
        select(func.count(AuthEvent.id)).where(*failure_filters)
    ) or 0
    if recent_failures >= LOGIN_FAILURE_LIMIT:
        raise AuthenticationError("Too many sign-in attempts. Try again later.")

    matching_memberships = []
    if "@" not in normalized:
        matching_memberships = list(
            session.scalars(
                select(RoleAssignment)
                .join(User, User.id == RoleAssignment.user_id)
                .where(
                    func.lower(RoleAssignment.username) == normalized,
                    RoleAssignment.is_active.is_(True),
                )
                .order_by(RoleAssignment.business_id)
            )
        )
    if len(matching_memberships) > 1:
        raise AuthenticationError(
            "That username is used by multiple businesses. Sign in with your email."
        )
    if matching_memberships:
        assignment = matching_memberships[0]
        user = session.get(User, assignment.user_id)
    else:
        user = session.scalar(
            select(User).where(func.lower(User.email) == normalized)
        )
        assignment = (
            session.scalar(
                select(RoleAssignment).where(
                    RoleAssignment.user_id == user.id,
                    RoleAssignment.is_active.is_(True),
                ).order_by(RoleAssignment.business_id)
            )
            if user else None
        )
    if user is not None and user.locked_until and user.locked_until > now:
        raise AuthenticationError("Too many sign-in attempts. Try again later.")

    stored_hash = user.password_hash if user else _DUMMY_PASSWORD_HASH
    valid = verify_password(password, stored_hash)
    if user is not None and assignment is not None and assignment.user_id != user.id:
        assignment = None
    if user is None or not valid or not user.is_active or assignment is None:
        if user is not None:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= LOGIN_FAILURE_LIMIT:
                user.locked_until = now + timedelta(minutes=LOGIN_LOCK_MINUTES)
        session.add(
            AuthEvent(
                user_id=user.id if user else None,
                identifier_hash=identifier_hash,
                event_type="login_failed",
                created_at=now,
            )
        )
        session.flush()
        if user and user.locked_until and user.locked_until > now:
            raise AuthenticationError("Too many sign-in attempts. Try again later.")
        raise AuthenticationError("Username/email or password is not correct.")

    if password_hash_needs_upgrade(user.password_hash):
        user.password_hash = hash_password(password)
    user.failed_login_attempts = 0
    user.locked_until = None
    session.add(
        AuthEvent(
            user_id=user.id,
            identifier_hash=identifier_hash,
            event_type="login_succeeded",
            created_at=now,
        )
    )
    session.add(
        AuditLog(
            business_id=assignment.business_id,
            user_id=user.id,
            action="login",
            details="User signed in.",
        )
    )
    session.flush()
    return user, assignment.role, assignment.business_id


@_identity_operation
def create_auth_session(
    session, user: User, remember_device: bool, business_id: int | None = None
):
    now = lagos_now()
    if business_id is None:
        business_id = user.business_id
    if business_id is None or session.scalar(
        select(RoleAssignment.id).where(
            RoleAssignment.user_id == user.id,
            RoleAssignment.business_id == business_id,
            RoleAssignment.is_active.is_(True),
        )
    ) is None:
        raise PermissionError("A valid business membership is required.")
    lifetime = (
        timedelta(days=REMEMBERED_SESSION_DAYS)
        if remember_device
        else timedelta(hours=SESSION_LIFETIME_HOURS)
    )
    raw_token = secrets.token_urlsafe(32)
    auth_session = AuthSession(
        user_id=user.id,
        business_id=business_id,
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        created_at=now,
        expires_at=now + lifetime,
        remember_device=remember_device,
    )
    session.add(auth_session)
    session.flush()
    return raw_token, auth_session


@_identity_operation
def resolve_auth_session(session, raw_token: str):
    if not raw_token:
        return None
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    auth_session = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == token_hash)
    )
    now = lagos_now()
    if (
        auth_session is None
        or auth_session.revoked_at is not None
        or auth_session.expires_at <= now
    ):
        return None
    user = session.get(User, auth_session.user_id)
    if user is None or not user.is_active:
        return None
    assignment = session.scalar(
        select(RoleAssignment).where(
            RoleAssignment.user_id == user.id,
            RoleAssignment.business_id == auth_session.business_id,
            RoleAssignment.is_active.is_(True),
        )
    )
    business = session.get(Business, auth_session.business_id)
    if assignment is None or business is None:
        return None
    return user, business, assignment.role, auth_session


@_identity_operation
def revoke_auth_session(session, raw_token: str) -> None:
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    auth_session = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == token_hash)
    )
    if auth_session and auth_session.revoked_at is None:
        auth_session.revoked_at = lagos_now()


@_identity_operation
def revoke_all_sessions(session, user_id: int) -> None:
    now = lagos_now()
    sessions = session.scalars(
        select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
    )
    for auth_session in sessions:
        auth_session.revoked_at = now
        session.add(
            AuditLog(
                business_id=auth_session.business_id,
                user_id=user_id,
                action="logout_all_sessions",
                details="User signed out all active sessions.",
            )
        )


@_identity_operation
def create_password_reset(session, email: str):
    normalized_email = email.strip().casefold()
    identifier_hash = _identifier_hash(normalized_email)
    now = lagos_now()
    session.add(
        AuthEvent(
            identifier_hash=identifier_hash,
            event_type="password_reset_requested",
            created_at=now,
        )
    )
    session.flush()
    requests = session.scalar(
        select(func.count(AuthEvent.id)).where(
            AuthEvent.identifier_hash == identifier_hash,
            AuthEvent.event_type == "password_reset_requested",
            AuthEvent.created_at
            >= now - timedelta(minutes=RESET_REQUEST_WINDOW_MINUTES),
        )
    ) or 0
    if requests > RESET_REQUEST_LIMIT:
        return None
    user = session.scalar(
        select(User).where(func.lower(User.email) == normalized_email)
    )
    if user is None or not user.is_active:
        return None
    for previous in session.scalars(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
    ):
        previous.used_at = now
    raw_token = secrets.token_urlsafe(32)
    session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            created_at=now,
            expires_at=now + timedelta(minutes=PASSWORD_RESET_MINUTES),
        )
    )
    session.flush()
    return raw_token


def send_password_reset_email(email: str, reset_link: str) -> None:
    import os

    host = os.getenv("SKA_SMTP_HOST", "").strip()
    sender = os.getenv("SKA_SMTP_FROM", os.getenv("SKA_SMTP_USER", "")).strip()
    if not host or not sender:
        raise RuntimeError("Password reset email delivery is not configured.")
    port = int(os.getenv("SKA_SMTP_PORT", "587"))
    message = EmailMessage()
    message["Subject"] = "Reset your Smart Kiosk Accounting password"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        "Use this single-use link to reset your password. "
        f"It expires in {PASSWORD_RESET_MINUTES} minutes:\n\n{reset_link}\n"
    )
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.starttls()
        username = os.getenv("SKA_SMTP_USER", "")
        password = os.getenv("SKA_SMTP_PASSWORD", "")
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


@_identity_operation
def reset_password(session, raw_token: str, new_password: str) -> None:
    validate_password(new_password)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    token = session.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash
        )
    )
    now = lagos_now()
    if token is None or token.used_at is not None or token.expires_at <= now:
        raise ValueError("This reset link is invalid or has expired. Request a new one.")
    user = session.get(User, token.user_id)
    if user is None or not user.is_active:
        raise ValueError("This reset link is invalid or has expired. Request a new one.")
    user.password_hash = hash_password(new_password)
    user.failed_login_attempts = 0
    user.locked_until = None
    token.used_at = now
    revoke_all_sessions(session, user.id)
    reset_hashes = {_identifier_hash(user.username)}
    if user.email:
        reset_hashes.add(_identifier_hash(user.email))
    session.add_all(
        AuthEvent(
            user_id=user.id,
            identifier_hash=identifier_hash,
            event_type="login_counter_reset",
            created_at=now,
        )
        for identifier_hash in reset_hashes
    )
    session.flush()
