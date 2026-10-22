"""Sprint 6 (Fase 5): mapa da planta, assistente de implantação, comandos do gateway, idempotência, demo

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-10-07 10:00:00.000000

* plant_layouts / layout_markers: planta baixa + marcadores normalizados (docs/sprint6.md §1.1);
* onboarding_progress: estado dos passos do assistente por tenant (§4);
* gateway_commands: comandos puxados pelo gateway no heartbeat (navegação OPC UA, §4.2);
* audit_events: trilha de auditoria genérica (publicação de layout, ações do demo-bot);
* client_actions: chaves de idempotência X-Client-Action-Id do tablet do operador (§3.2);
* demo_sessions + tenants.is_demo: demonstração pública somente leitura (§5).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'a4b5c6d7e8f9'
down_revision = 'f3a4b5c6d7e8'
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=False)
JSONB = postgresql.JSONB()
TS = sa.DateTime(timezone=True)


def _stamps():
    return [sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', TS, server_default=sa.text('now()'), nullable=False)]


def upgrade() -> None:
    op.add_column('tenants', sa.Column('is_demo', sa.Boolean(), nullable=False, server_default=sa.text('false')))

    op.create_table(
        'plant_layouts',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plant_id', UUID, sa.ForeignKey('plants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('image_asset_id', sa.String(64), nullable=True),
        sa.Column('image_content_type', sa.String(64), nullable=True),
        sa.Column('image_bytes', sa.Integer(), nullable=True),
        sa.Column('image_width_px', sa.Integer(), nullable=True),
        sa.Column('image_height_px', sa.Integer(), nullable=True),
        sa.Column('scale_m_per_px', sa.Float(), nullable=True),
        sa.Column('needs_review', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_by', UUID, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('published_by', UUID, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('published_at', TS, nullable=True),
        *_stamps(),
        sa.CheckConstraint("status IN ('draft', 'published', 'archived')", name='ck_plant_layouts_status'),
        sa.UniqueConstraint('plant_id', 'version', name='uq_plant_layouts_plant_version'),
    )
    op.create_index('ix_plant_layouts_tenant_id', 'plant_layouts', ['tenant_id'])
    op.create_index('ix_plant_layouts_plant_id', 'plant_layouts', ['plant_id'])
    op.create_index('uq_plant_layouts_one_published', 'plant_layouts', ['plant_id'], unique=True,
                    postgresql_where=sa.text("status = 'published'"))
    op.create_index('uq_plant_layouts_one_draft', 'plant_layouts', ['plant_id'], unique=True,
                    postgresql_where=sa.text("status = 'draft'"))

    op.create_table(
        'layout_markers',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('layout_id', UUID, sa.ForeignKey('plant_layouts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('machine_id', UUID, sa.ForeignKey('machines.id', ondelete='CASCADE'), nullable=True),
        sa.Column('line_id', UUID, sa.ForeignKey('lines.id', ondelete='CASCADE'), nullable=True),
        sa.Column('label', sa.String(120), nullable=True),
        sa.Column('x', sa.Float(), nullable=False),
        sa.Column('y', sa.Float(), nullable=False),
        sa.Column('rotation_deg', sa.Float(), nullable=False, server_default=sa.text('0')),
        sa.Column('size', sa.String(1), nullable=False, server_default='m'),
        *_stamps(),
        sa.CheckConstraint("kind IN ('machine', 'line_label', 'area_label')", name='ck_layout_markers_kind'),
        sa.CheckConstraint("size IN ('s', 'm', 'l')", name='ck_layout_markers_size'),
        sa.CheckConstraint('x >= 0 AND x <= 1 AND y >= 0 AND y <= 1', name='ck_layout_markers_xy'),
        sa.CheckConstraint('rotation_deg >= 0 AND rotation_deg < 360', name='ck_layout_markers_rotation'),
        sa.CheckConstraint("(kind = 'machine') = (machine_id IS NOT NULL)", name='ck_layout_markers_machine'),
        sa.CheckConstraint("(kind = 'line_label') = (line_id IS NOT NULL)", name='ck_layout_markers_line'),
    )
    op.create_index('ix_layout_markers_tenant_id', 'layout_markers', ['tenant_id'])
    op.create_index('ix_layout_markers_layout_id', 'layout_markers', ['layout_id'])
    op.create_index('uq_layout_markers_machine', 'layout_markers', ['layout_id', 'machine_id'], unique=True,
                    postgresql_where=sa.text('machine_id IS NOT NULL'))

    op.create_table(
        'onboarding_progress',
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('plant_id', UUID, sa.ForeignKey('plants.id', ondelete='SET NULL'), nullable=True),
        sa.Column('steps', JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('updated_by', UUID, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('ready_at', TS, nullable=True),
        *_stamps(),
    )

    op.create_table(
        'gateway_commands',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('gateway_id', UUID, sa.ForeignKey('gateways.id', ondelete='CASCADE'), nullable=False),
        sa.Column('type', sa.String(32), nullable=False),
        sa.Column('params', JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('result', JSONB, nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_by', UUID, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('expires_at', TS, nullable=False),
        sa.Column('sent_at', TS, nullable=True),
        sa.Column('completed_at', TS, nullable=True),
        *_stamps(),
        sa.CheckConstraint("type IN ('opcua_browse')", name='ck_gateway_commands_type'),
        sa.CheckConstraint("status IN ('pending', 'sent', 'done', 'failed', 'expired')",
                           name='ck_gateway_commands_status'),
    )
    op.create_index('ix_gateway_commands_tenant_id', 'gateway_commands', ['tenant_id'])
    op.create_index('ix_gateway_commands_gateway_status', 'gateway_commands', ['gateway_id', 'status'])

    op.create_table(
        'audit_events',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', UUID, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('action', sa.String(64), nullable=False),
        sa.Column('entity_type', sa.String(32), nullable=False),
        sa.Column('entity_id', sa.String(64), nullable=True),
        sa.Column('data', JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_audit_events_tenant_created', 'audit_events', ['tenant_id', 'created_at'])

    op.create_table(
        'client_actions',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', UUID, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('action_id', sa.String(64), nullable=False),
        sa.Column('endpoint', sa.String(64), nullable=False),
        sa.Column('entity_id', sa.String(64), nullable=True),
        sa.Column('status_code', sa.Integer(), nullable=False, server_default=sa.text('200')),
        sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('tenant_id', 'user_id', 'action_id', name='uq_client_actions_key'),
    )

    op.create_table(
        'demo_sessions',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('ip_hash', sa.String(64), nullable=False),
        sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', TS, nullable=False),
        sa.Column('ai_questions_used', sa.Integer(), nullable=False, server_default=sa.text('0')),
    )
    op.create_index('ix_demo_sessions_ip_created', 'demo_sessions', ['ip_hash', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_demo_sessions_ip_created', table_name='demo_sessions')
    op.drop_table('demo_sessions')
    op.drop_table('client_actions')
    op.drop_index('ix_audit_events_tenant_created', table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_index('ix_gateway_commands_gateway_status', table_name='gateway_commands')
    op.drop_index('ix_gateway_commands_tenant_id', table_name='gateway_commands')
    op.drop_table('gateway_commands')
    op.drop_table('onboarding_progress')
    op.drop_index('uq_layout_markers_machine', table_name='layout_markers')
    op.drop_index('ix_layout_markers_layout_id', table_name='layout_markers')
    op.drop_index('ix_layout_markers_tenant_id', table_name='layout_markers')
    op.drop_table('layout_markers')
    op.drop_index('uq_plant_layouts_one_draft', table_name='plant_layouts')
    op.drop_index('uq_plant_layouts_one_published', table_name='plant_layouts')
    op.drop_index('ix_plant_layouts_plant_id', table_name='plant_layouts')
    op.drop_index('ix_plant_layouts_tenant_id', table_name='plant_layouts')
    op.drop_table('plant_layouts')
    op.drop_column('tenants', 'is_demo')
