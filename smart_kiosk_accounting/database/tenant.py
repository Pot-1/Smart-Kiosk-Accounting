from contextlib import contextmanager

from sqlalchemy import event, inspect as sa_inspect, or_, select
from sqlalchemy.orm import Session, with_loader_criteria

from database.models import Base, Business, BusinessMembership, User


_TENANT_CLASSES = tuple(
    mapper.class_
    for mapper in Base.registry.mappers
    if "business_id" in mapper.column_attrs and mapper.class_.__name__ != "User"
)


class TenantSession(Session):
    def get(self, entity, ident, **kwargs):
        business_id = self.info.get("tenant_business_id")
        if business_id is None or self.info.get("system_operation") is True:
            return super().get(entity, ident, **kwargs)
        mapped_class = sa_inspect(entity).class_
        identity = self.identity_key(mapped_class, ident)
        cached = self.identity_map.get(identity)
        if cached is not None:
            record_business_id = getattr(cached, "business_id", None)
            if record_business_id is not None and record_business_id != business_id:
                return None
            if isinstance(cached, Business) and cached.id != business_id:
                return None
            if mapped_class.__name__ == "User":
                user_id = self.info.get("tenant_user_id")
                if cached.id != user_id:
                    from database.models import BusinessMembership

                    member = self.scalar(
                        select(BusinessMembership.id).where(
                            BusinessMembership.user_id == cached.id,
                            BusinessMembership.is_active.is_(True),
                        )
                    )
                    if member is None:
                        return None
        return super().get(entity, ident, **kwargs)


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_criteria(execute_state):
    session = execute_state.session
    if session.info.get("system_operation") is True:
        return
    business_id = session.info.get("tenant_business_id")
    if business_id is None:
        if (
            execute_state.is_select
            or execute_state.is_update
            or execute_state.is_delete
            or execute_state.is_insert
        ):
            raise PermissionError(
                "Tenant data queries require an authenticated business context."
            )
        return
    if not execute_state.is_orm_statement:
        raise PermissionError(
            "Tenant-scoped sessions cannot execute unscoped SQL statements."
        )
    statement = execute_state.statement.options(
        with_loader_criteria(
            Business,
            lambda model: model.id == business_id,
            include_aliases=True,
        )
    )
    for model in _TENANT_CLASSES:
        statement = statement.options(
            with_loader_criteria(
                model,
                lambda entity: entity.business_id == business_id,
                include_aliases=True,
            )
        )
    tenant_user_id = session.info.get("tenant_user_id")
    if (
        tenant_user_id is not None
        and not execute_state.execution_options.get("tenant_user_lookup")
    ):
        from database.models import BusinessMembership, User

        statement = statement.options(
            with_loader_criteria(
                User,
                lambda model: or_(
                    model.id == tenant_user_id,
                    model.id.in_(
                        select(BusinessMembership.user_id).where(
                            BusinessMembership.business_id == business_id
                        )
                    ),
                ),
                include_aliases=True,
            )
        )
    execute_state.statement = statement


@event.listens_for(Session, "before_flush")
def _validate_tenant_writes(session, _flush_context, _instances):
    if session.info.get("system_operation") is True:
        return
    business_id = session.info.get("tenant_business_id")
    if business_id is None:
        if session.new or session.dirty or session.deleted:
            raise PermissionError(
                "Writes require an authenticated business context."
            )
        return
    tenant_user_id = session.info.get("tenant_user_id")
    scoped_writes = [
        instance
        for instance in (*session.new, *session.dirty, *session.deleted)
        if (
            isinstance(instance, (Business, User))
            or getattr(instance, "business_id", None) is not None
        )
    ]
    if scoped_writes:
        if tenant_user_id is None:
            raise PermissionError("An authenticated business membership is required.")
        membership = session.connection().execute(
            select(BusinessMembership.id).where(
                BusinessMembership.user_id == tenant_user_id,
                BusinessMembership.business_id == business_id,
                BusinessMembership.is_active.is_(True),
            )
        ).first()
        if membership is None:
            raise PermissionError("You are not an active member of this business.")
    for instance in (*session.new, *session.dirty, *session.deleted):
        if isinstance(instance, User):
            if (
                instance not in session.new
                and instance.id != tenant_user_id
            ):
                raise PermissionError("You cannot modify another user's account.")
            continue
        state = getattr(instance, "__dict__", {})
        model_business_id = state.get("business_id")
        if model_business_id is not None and model_business_id != business_id:
            raise PermissionError("This record belongs to a different business.")
        if isinstance(instance, Business) and instance.id not in (None, business_id):
            raise PermissionError("You cannot modify another business.")


def require_business_access(
    session: Session, business_id: int, permission: str | None = None
) -> None:
    tenant_business_id = session.info.get("tenant_business_id")
    user_id = session.info.get("tenant_user_id")
    if session.info.get("system_operation") is True:
        return
    if tenant_business_id is None or user_id is None:
        raise PermissionError("An authenticated business membership is required.")
    if business_id != tenant_business_id:
        raise PermissionError("This action is outside your active business.")
    from database.models import BusinessMembership
    from utils.authorization import user_can

    membership = session.scalar(
        select(BusinessMembership.id).where(
            BusinessMembership.user_id == user_id,
            BusinessMembership.business_id == business_id,
            BusinessMembership.is_active.is_(True),
        )
    )
    if membership is None:
        raise PermissionError("You are not a member of this business.")
    if permission and not user_can(session, user_id, business_id, permission):
        raise PermissionError("Your business role does not allow this action.")


@contextmanager
def tenant_system_operation(session: Session):
    previous = session.info.get("system_operation")
    session.info["system_operation"] = True
    try:
        yield
    finally:
        if previous is None:
            session.info.pop("system_operation", None)
        else:
            session.info["system_operation"] = previous
