"""add_cameras

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-04-06 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c4d5e6f7a8b9'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'cameras',
        sa.Column('id',          sa.UUID(as_uuid=False), nullable=False),
        sa.Column('tenant_id',   sa.UUID(as_uuid=False), nullable=False),
        sa.Column('machine_id',  sa.UUID(as_uuid=False), nullable=True),
        sa.Column('name',        sa.String(length=255),  nullable=False),
        sa.Column('rtsp_url',    sa.Text(),               nullable=False),
        sa.Column('description', sa.String(length=500),  nullable=True),
        sa.Column('is_active',   sa.Boolean(),            nullable=False, server_default='true'),
        sa.Column('created_at',  sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at',  sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'],  ['tenants.id'],  ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['machine_id'], ['machines.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cameras_tenant_id',  'cameras', ['tenant_id'])
    op.create_index('ix_cameras_machine_id', 'cameras', ['machine_id'])


def downgrade() -> None:
    op.drop_index('ix_cameras_machine_id', table_name='cameras')
    op.drop_index('ix_cameras_tenant_id',  table_name='cameras')
    op.drop_table('cameras')
