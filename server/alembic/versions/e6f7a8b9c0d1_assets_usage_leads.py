"""Fase 1: plantas, posições, componentes, papel semântico das tags, acumuladores de uso e leads

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-10-05 12:00:00.000000

* plants (+ backfill: uma planta padrão por tenant; lines.plant_id passa a ser obrigatório)
* lines.output_machine_id / bottleneck_machine_id, machines.ideal_cycle_time_s
* positions, components (no máximo um instalado por posição — índice único parcial)
* tags.semantic_role / position_id / counter_max / max_rate_per_s
* tag_states, usage_totals, usage_buckets, usage_events (domain.md §3.3–3.5)
* leads (site público, sem tenant)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'e6f7a8b9c0d1'
down_revision = 'd5e6f7a8b9c0'
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=False)

COMPONENT_TYPES = ('pneumatic_cylinder', 'electric_motor', 'servo', 'robot_axis', 'valve', 'gripper',
                   'bearing', 'belt', 'pump', 'spindle', 'other')
REMOVAL_REASONS = ('failure', 'preventive_replacement', 'upgrade', 'other')
SEMANTIC_ROLES = ('analog', 'cycle_counter', 'cycle_pulse', 'run_state', 'part_counter_good',
                  'part_counter_scrap', 'part_counter_total', 'fault_state', 'stroke_time', 'speed', 'current')

componenttype = postgresql.ENUM(*COMPONENT_TYPES, name='componenttype', create_type=False)
removalreason = postgresql.ENUM(*REMOVAL_REASONS, name='removalreason', create_type=False)
semanticrole = postgresql.ENUM(*SEMANTIC_ROLES, name='semanticrole', create_type=False)


def _ts(name, nullable=False, default=True):
    kw = {'server_default': sa.text('now()')} if default else {}
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, **kw)


def upgrade() -> None:
    bind = op.get_bind()
    componenttype.create(bind, checkfirst=True)
    removalreason.create(bind, checkfirst=True)
    semanticrole.create(bind, checkfirst=True)

    # ── plants ────────────────────────────────────────────────────────
    op.create_table(
        'plants',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('timezone', sa.String(64), nullable=False, server_default='America/Sao_Paulo'),
        sa.Column('address', sa.String(500), nullable=True),
        _ts('created_at'), _ts('updated_at'),
    )
    op.create_index('ix_plants_tenant_id', 'plants', ['tenant_id'])

    # ── lines: plant_id (backfill), output/bottleneck machine ─────────
    op.add_column('lines', sa.Column('plant_id', UUID, nullable=True))
    op.execute("""
        INSERT INTO plants (id, tenant_id, name, timezone)
        SELECT gen_random_uuid(), t.id, 'Planta principal', 'America/Sao_Paulo'
          FROM tenants t
    """)
    op.execute("""
        UPDATE lines l SET plant_id = p.id
          FROM plants p WHERE p.tenant_id = l.tenant_id
    """)
    op.alter_column('lines', 'plant_id', nullable=False)
    op.create_foreign_key('fk_lines_plant_id', 'lines', 'plants', ['plant_id'], ['id'], ondelete='RESTRICT')
    op.create_index('ix_lines_plant_id', 'lines', ['plant_id'])
    op.add_column('lines', sa.Column('output_machine_id', UUID, nullable=True))
    op.add_column('lines', sa.Column('bottleneck_machine_id', UUID, nullable=True))
    op.create_foreign_key('fk_lines_output_machine_id', 'lines', 'machines', ['output_machine_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_lines_bottleneck_machine_id', 'lines', 'machines', ['bottleneck_machine_id'], ['id'], ondelete='SET NULL')

    op.add_column('machines', sa.Column('ideal_cycle_time_s', sa.Float, nullable=True))

    # ── positions / components ────────────────────────────────────────
    op.create_table(
        'positions',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('machine_id', UUID, sa.ForeignKey('machines.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('component_type', componenttype, nullable=False),
        sa.Column('cycles_per_part', sa.Integer, nullable=False, server_default='1'),
        sa.Column('counter_tag_id', UUID, sa.ForeignKey('tags.id', ondelete='SET NULL'), nullable=True),
        _ts('created_at'), _ts('updated_at'),
    )
    op.create_index('ix_positions_tenant_id', 'positions', ['tenant_id'])
    op.create_index('ix_positions_machine_id', 'positions', ['machine_id'])

    op.create_table(
        'components',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('position_id', UUID, sa.ForeignKey('positions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('last_position_id', UUID, sa.ForeignKey('positions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('component_type', componenttype, nullable=False),
        sa.Column('manufacturer', sa.String(255), nullable=True),
        sa.Column('model', sa.String(255), nullable=True),
        sa.Column('serial_number', sa.String(255), nullable=True),
        sa.Column('installed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('removal_reason', removalreason, nullable=True),
        sa.Column('nominal_life', postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts('created_at'), _ts('updated_at'),
    )
    op.create_index('ix_components_tenant_id', 'components', ['tenant_id'])
    op.create_index('ix_components_position_id', 'components', ['position_id'])
    op.create_index('ix_components_last_position_id', 'components', ['last_position_id'])
    op.create_index('uq_components_one_installed_per_position', 'components', ['position_id'], unique=True,
                    postgresql_where=sa.text('position_id IS NOT NULL AND removed_at IS NULL'))

    # ── tags: semantic role ───────────────────────────────────────────
    op.add_column('tags', sa.Column('semantic_role', semanticrole, nullable=False, server_default='analog'))
    op.add_column('tags', sa.Column('position_id', UUID, nullable=True))
    op.add_column('tags', sa.Column('counter_max', sa.BigInteger, nullable=True))
    op.add_column('tags', sa.Column('max_rate_per_s', sa.Float, nullable=True))
    op.create_foreign_key('fk_tags_position_id', 'tags', 'positions', ['position_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_tags_position_id', 'tags', ['position_id'])

    # ── usage ─────────────────────────────────────────────────────────
    op.create_table(
        'tag_states',
        sa.Column('tag_id', UUID, sa.ForeignKey('tags.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('last_value', sa.Float(precision=53), nullable=False),
        sa.Column('last_ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('pending_value', sa.Float(precision=53), nullable=True),
        sa.Column('pending_ts', sa.DateTime(timezone=True), nullable=True),
        sa.Column('off_since', sa.DateTime(timezone=True), nullable=True),
        _ts('updated_at'),
    )
    op.create_index('ix_tag_states_tenant_id', 'tag_states', ['tenant_id'])

    op.create_table(
        'usage_totals',
        sa.Column('scope', sa.String(16), primary_key=True),
        sa.Column('entity_id', UUID, primary_key=True),
        sa.Column('measure', sa.String(32), primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('value', sa.Float(precision=53), nullable=False, server_default='0'),
        _ts('updated_at'),
    )
    op.create_index('ix_usage_totals_tenant_id', 'usage_totals', ['tenant_id'])

    op.create_table(
        'usage_buckets',
        sa.Column('scope', sa.String(16), primary_key=True),
        sa.Column('entity_id', UUID, primary_key=True),
        sa.Column('measure', sa.String(32), primary_key=True),
        sa.Column('bucket_start', sa.DateTime(timezone=True), primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('value', sa.Float(precision=53), nullable=False, server_default='0'),
    )
    op.create_index('ix_usage_buckets_tenant_bucket', 'usage_buckets', ['tenant_id', 'bucket_start'])

    op.create_table(
        'usage_events',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tag_id', UUID, sa.ForeignKey('tags.id', ondelete='CASCADE'), nullable=False),
        sa.Column('event', sa.String(32), nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.Column('prev_value', sa.Float(precision=53), nullable=True),
        sa.Column('value', sa.Float(precision=53), nullable=True),
        sa.Column('delta', sa.Float(precision=53), nullable=True),
        sa.Column('details', postgresql.JSONB, nullable=True),
        _ts('created_at'),
    )
    op.create_index('ix_usage_events_tenant_ts', 'usage_events', ['tenant_id', 'ts'])
    op.create_index('ix_usage_events_tag_id', 'usage_events', ['tag_id'])

    # ── leads ─────────────────────────────────────────────────────────
    op.create_table(
        'leads',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('email', sa.String(320), nullable=False),
        sa.Column('company', sa.String(255), nullable=True),
        sa.Column('phone', sa.String(64), nullable=True),
        sa.Column('message', sa.Text, nullable=True),
        sa.Column('consent', sa.Boolean, nullable=False),
        sa.Column('consent_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source', sa.String(255), nullable=True),
        sa.Column('ip_hash', sa.String(64), nullable=False),
        sa.Column('user_agent', sa.String(500), nullable=True),
        sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notify_error', sa.String(500), nullable=True),
        _ts('created_at'),
    )
    op.create_index('ix_leads_ip_hash_created', 'leads', ['ip_hash', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_leads_ip_hash_created', table_name='leads')
    op.drop_table('leads')
    op.drop_table('usage_events')
    op.drop_table('usage_buckets')
    op.drop_table('usage_totals')
    op.drop_table('tag_states')

    op.drop_index('ix_tags_position_id', table_name='tags')
    op.drop_constraint('fk_tags_position_id', 'tags', type_='foreignkey')
    op.drop_column('tags', 'max_rate_per_s')
    op.drop_column('tags', 'counter_max')
    op.drop_column('tags', 'position_id')
    op.drop_column('tags', 'semantic_role')

    op.drop_table('components')
    op.drop_table('positions')

    op.drop_column('machines', 'ideal_cycle_time_s')
    op.drop_constraint('fk_lines_bottleneck_machine_id', 'lines', type_='foreignkey')
    op.drop_constraint('fk_lines_output_machine_id', 'lines', type_='foreignkey')
    op.drop_column('lines', 'bottleneck_machine_id')
    op.drop_column('lines', 'output_machine_id')
    op.drop_index('ix_lines_plant_id', table_name='lines')
    op.drop_constraint('fk_lines_plant_id', 'lines', type_='foreignkey')
    op.drop_column('lines', 'plant_id')
    op.drop_index('ix_plants_tenant_id', table_name='plants')
    op.drop_table('plants')

    bind = op.get_bind()
    semanticrole.drop(bind, checkfirst=True)
    removalreason.drop(bind, checkfirst=True)
    componenttype.drop(bind, checkfirst=True)
