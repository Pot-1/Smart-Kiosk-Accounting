"""Migrate to business membership and tenant-scoped records."""

from alembic import op

from database.migrations import upgrade_tenant_schema

revision = "0001_multi_tenant"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    upgrade_tenant_schema(op.get_bind())


def downgrade():
    raise RuntimeError(
        "This migration changes ownership and uniqueness constraints. "
        "Restore a verified pre-migration backup instead of downgrading."
    )
