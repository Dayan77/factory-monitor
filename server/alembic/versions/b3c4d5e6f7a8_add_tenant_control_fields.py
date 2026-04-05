"""add_tenant_control_fields

Revision ID: b3c4d5e6f7a8
Revises: 82844e19a3d9
Create Date: 2026-04-04 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'b3c4d5e6f7a8'
down_revision = '82844e19a3d9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TYPE tenantplan AS ENUM ('trial', 'starter', 'professional', 'enterprise')")

    op.add_column('tenants', sa.Column('is_active',        sa.Boolean(),                   nullable=False, server_default='true'))
    op.add_column('tenants', sa.Column('plan',             sa.Enum('trial', 'starter', 'professional', 'enterprise', name='tenantplan'), nullable=False, server_default='trial'))
    op.add_column('tenants', sa.Column('trial_ends_at',    sa.DateTime(timezone=True),     nullable=True))
    op.add_column('tenants', sa.Column('suspended_reason', sa.String(length=500),          nullable=True))
    op.add_column('tenants', sa.Column('max_users',        sa.Integer(),                   nullable=False, server_default='5'))
    op.add_column('tenants', sa.Column('max_machines',     sa.Integer(),                   nullable=False, server_default='10'))


def downgrade() -> None:
    op.drop_column('tenants', 'max_machines')
    op.drop_column('tenants', 'max_users')
    op.drop_column('tenants', 'suspended_reason')
    op.drop_column('tenants', 'trial_ends_at')
    op.drop_column('tenants', 'plan')
    op.drop_column('tenants', 'is_active')
    op.execute("DROP TYPE tenantplan")
