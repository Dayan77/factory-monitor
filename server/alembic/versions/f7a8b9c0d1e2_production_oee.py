"""Fase 2: turnos/calendário, produtos e PRI, motivos de parada, paradas, falhas, micro-paradas

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-10-06 12:00:00.000000

* machines.micro_stop_threshold_s (padrão 120) e machines.fault_code_map
* shifts, calendar_exceptions, products, machine_products, production_runs
* reason_codes (+ seed da taxonomia padrão domain.md §4.4, tenant_id nulo)
* downtime_events, failure_records (rascunho ISO 14224), micro_stop_buckets,
  machine_data_spans, downtime_detector_states
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'f7a8b9c0d1e2'
down_revision = 'e6f7a8b9c0d1'
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=False)

# domain.md §4.4 — (code, category, label, planned)
DEFAULT_REASON_CODES = [
    ("PLN-01", "PLN", "Refeição / pausa", True),
    ("PLN-02", "PLN", "Reunião / treinamento / 5S programado", True),
    ("PLN-03", "PLN", "Manutenção preventiva programada", True),
    ("PLN-04", "PLN", "Sem programação / sem demanda", True),
    ("PLN-05", "PLN", "Teste / try-out / engenharia programada", True),
    ("EQP-01", "EQP", "Falha mecânica", False),
    ("EQP-02", "EQP", "Falha elétrica", False),
    ("EQP-03", "EQP", "Falha pneumática/hidráulica", False),
    ("EQP-04", "EQP", "Falha de automação/software", False),
    ("EQP-05", "EQP", "Ferramenta/molde/dispositivo", False),
    ("SET-01", "SET", "Setup / troca de produto", False),
    ("SET-02", "SET", "Ajuste / regulagem após setup", False),
    ("MAT-01", "MAT", "Falta de material / bloqueio por montante (starved)", False),
    ("MAT-02", "MAT", "Saída bloqueada por jusante (blocked)", False),
    ("OPR-01", "OPR", "Falta de operador", False),
    ("QLT-01", "QLT", "Aguardando inspeção / problema de qualidade", False),
    ("UTL-01", "UTL", "Utilidades (energia, ar comprimido, água)", False),
    ("MNT-02", "MNT", "Manutenção não programada fora de falha", False),
    ("UNK-01", "UNK", "Não justificada", False),
]


def _ts(name, nullable=False, default=True):
    kw = {'server_default': sa.text('now()')} if default else {}
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, **kw)


def _tenant():
    return sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False)


def upgrade() -> None:
    # A tag is the counter of at most one position (Sprint 2 review): keep the oldest link.
    op.execute("""
        UPDATE positions p SET counter_tag_id = NULL
         WHERE counter_tag_id IS NOT NULL AND EXISTS (
               SELECT 1 FROM positions q
                WHERE q.counter_tag_id = p.counter_tag_id
                  AND (q.created_at, q.id) < (p.created_at, p.id))
    """)
    op.create_index('uq_positions_counter_tag_id', 'positions', ['counter_tag_id'], unique=True,
                    postgresql_where=sa.text('counter_tag_id IS NOT NULL'))

    op.add_column('machines', sa.Column('micro_stop_threshold_s', sa.Integer(), nullable=False,
                                        server_default='120'))
    op.add_column('machines', sa.Column('fault_code_map', postgresql.JSONB(), nullable=False,
                                        server_default=sa.text("'{}'::jsonb")))

    op.create_table(
        'shifts',
        sa.Column('id', UUID, primary_key=True), _tenant(),
        sa.Column('plant_id', UUID, sa.ForeignKey('plants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('start_local', sa.String(5), nullable=False),
        sa.Column('end_local', sa.String(5), nullable=False),
        sa.Column('weekdays', postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column('breaks', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        _ts('created_at'), _ts('updated_at'),
    )
    op.create_index('ix_shifts_tenant_id', 'shifts', ['tenant_id'])
    op.create_index('ix_shifts_plant_id', 'shifts', ['plant_id'])

    op.create_table(
        'calendar_exceptions',
        sa.Column('id', UUID, primary_key=True), _tenant(),
        sa.Column('plant_id', UUID, sa.ForeignKey('plants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('shift_id', UUID, sa.ForeignKey('shifts.id', ondelete='CASCADE'), nullable=True),
        sa.Column('start_local', sa.String(5), nullable=True),
        sa.Column('end_local', sa.String(5), nullable=True),
        sa.Column('reason_code', sa.String(16), nullable=True),
        sa.Column('note', sa.String(500), nullable=True),
        _ts('created_at'), _ts('updated_at'),
        sa.CheckConstraint("kind IN ('holiday', 'extra_shift', 'planned_stop')", name='ck_calendar_exceptions_kind'),
    )
    op.create_index('ix_calendar_exceptions_tenant_id', 'calendar_exceptions', ['tenant_id'])
    op.create_index('ix_calendar_exceptions_plant_id', 'calendar_exceptions', ['plant_id'])

    op.create_table(
        'products',
        sa.Column('id', UUID, primary_key=True), _tenant(),
        sa.Column('code', sa.String(64), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        _ts('created_at'), _ts('updated_at'),
        sa.UniqueConstraint('tenant_id', 'code', name='uq_products_tenant_code'),
    )
    op.create_index('ix_products_tenant_id', 'products', ['tenant_id'])

    op.create_table(
        'machine_products',
        sa.Column('id', UUID, primary_key=True), _tenant(),
        sa.Column('machine_id', UUID, sa.ForeignKey('machines.id', ondelete='CASCADE'), nullable=False),
        sa.Column('product_id', UUID, sa.ForeignKey('products.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ideal_cycle_time_s', sa.Float(), nullable=False),
        sa.Column('parts_per_cycle', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        _ts('created_at'), _ts('updated_at'),
        sa.UniqueConstraint('machine_id', 'product_id', name='uq_machine_products_machine_product'),
        sa.CheckConstraint('ideal_cycle_time_s > 0', name='ck_machine_products_ict'),
        sa.CheckConstraint('parts_per_cycle >= 1', name='ck_machine_products_ppc'),
    )
    op.create_index('ix_machine_products_tenant_id', 'machine_products', ['tenant_id'])
    op.create_index('ix_machine_products_machine_id', 'machine_products', ['machine_id'])
    op.create_index('ix_machine_products_product_id', 'machine_products', ['product_id'])
    op.create_index('uq_machine_products_one_default', 'machine_products', ['machine_id'], unique=True,
                    postgresql_where=sa.text('is_default'))

    op.create_table(
        'production_runs',
        sa.Column('id', UUID, primary_key=True), _tenant(),
        sa.Column('machine_id', UUID, sa.ForeignKey('machines.id', ondelete='CASCADE'), nullable=False),
        sa.Column('product_id', UUID, sa.ForeignKey('products.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        _ts('created_at'), _ts('updated_at'),
    )
    op.create_index('ix_production_runs_tenant_id', 'production_runs', ['tenant_id'])
    op.create_index('ix_production_runs_machine_started', 'production_runs', ['machine_id', 'started_at'])
    op.create_index('uq_production_runs_one_open', 'production_runs', ['machine_id'], unique=True,
                    postgresql_where=sa.text('ended_at IS NULL'))

    reason_codes = op.create_table(
        'reason_codes',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=True),
        sa.Column('code', sa.String(16), nullable=False),
        sa.Column('category', sa.String(64), nullable=False),
        sa.Column('label', sa.String(255), nullable=False),
        sa.Column('planned', sa.Boolean(), nullable=False),
        sa.Column('oee_loss', sa.String(16), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        _ts('created_at'), _ts('updated_at'),
        sa.CheckConstraint("oee_loss IN ('none', 'availability', 'performance', 'quality')",
                           name='ck_reason_codes_oee_loss'),
    )
    op.create_index('ix_reason_codes_tenant_id', 'reason_codes', ['tenant_id'])
    op.create_index('uq_reason_codes_default_code', 'reason_codes', ['code'], unique=True,
                    postgresql_where=sa.text('tenant_id IS NULL'))
    op.create_index('uq_reason_codes_tenant_code', 'reason_codes', ['tenant_id', 'code'], unique=True,
                    postgresql_where=sa.text('tenant_id IS NOT NULL'))
    op.bulk_insert(reason_codes, [
        {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"factoryconnect:reason:{code}")), "tenant_id": None,
         "code": code, "category": cat, "label": label, "planned": planned,
         "oee_loss": "none" if planned else "availability", "is_active": True}
        for code, cat, label, planned in DEFAULT_REASON_CODES
    ])

    op.create_table(
        'downtime_events',
        sa.Column('id', UUID, primary_key=True), _tenant(),
        sa.Column('machine_id', UUID, sa.ForeignKey('machines.id', ondelete='CASCADE'), nullable=False),
        sa.Column('start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_s', sa.Float(), nullable=True),
        sa.Column('stop_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('source', sa.String(16), nullable=False),
        sa.Column('reason_code', sa.String(16), nullable=True),
        sa.Column('suggested_reason_code', sa.String(16), nullable=True),
        sa.Column('planned', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('fault_code', sa.Integer(), nullable=True),
        sa.Column('fault_since', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failure_id', UUID, nullable=True),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('shift_id', UUID, sa.ForeignKey('shifts.id', ondelete='SET NULL'), nullable=True),
        sa.Column('productive_day', sa.Date(), nullable=True),
        sa.Column('justified_by', UUID, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('justified_at', sa.DateTime(timezone=True), nullable=True),
        _ts('created_at'), _ts('updated_at'),
        sa.CheckConstraint("source IN ('auto_fault', 'auto_run_state', 'auto_cycle_gap', 'manual')",
                           name='ck_downtime_events_source'),
    )
    op.create_index('ix_downtime_events_tenant_machine_start', 'downtime_events', ['tenant_id', 'machine_id', 'start'])
    op.create_index('uq_downtime_events_auto_start', 'downtime_events', ['machine_id', 'start'], unique=True,
                    postgresql_where=sa.text("source <> 'manual'"))

    op.create_table(
        'failure_records',
        sa.Column('id', UUID, primary_key=True), _tenant(),
        sa.Column('machine_id', UUID, sa.ForeignKey('machines.id', ondelete='CASCADE'), nullable=False),
        sa.Column('position_id', UUID, sa.ForeignKey('positions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('downtime_event_id', UUID, sa.ForeignKey('downtime_events.id', ondelete='SET NULL'), nullable=True),
        sa.Column('failed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('restored_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('severity', sa.String(16), nullable=False, server_default='critical'),
        sa.Column('failure_mode', sa.String(8), nullable=True),
        sa.Column('cause', sa.String(16), nullable=True),
        sa.Column('mechanism', sa.String(16), nullable=True),
        sa.Column('detection_method', sa.String(64), nullable=True),
        sa.Column('status', sa.String(16), nullable=False, server_default='draft'),
        _ts('created_at'), _ts('updated_at'),
        sa.CheckConstraint("status IN ('draft', 'complete')", name='ck_failure_records_status'),
        sa.CheckConstraint("severity IN ('critical', 'degraded', 'incipient')", name='ck_failure_records_severity'),
    )
    op.create_index('ix_failure_records_tenant_machine_failed', 'failure_records',
                    ['tenant_id', 'machine_id', 'failed_at'])
    op.create_foreign_key('fk_downtime_events_failure_id', 'downtime_events', 'failure_records',
                          ['failure_id'], ['id'], ondelete='SET NULL')

    op.create_table(
        'micro_stop_buckets',
        sa.Column('machine_id', UUID, sa.ForeignKey('machines.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('bucket_start', sa.DateTime(timezone=True), primary_key=True),
        sa.Column('shift_slot', sa.String(36), primary_key=True),
        _tenant(),
        sa.Column('shift_id', UUID, nullable=True),
        sa.Column('productive_day', sa.Date(), nullable=True),
        sa.Column('piece_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('loss_s', sa.Float(precision=53), nullable=False, server_default='0'),
        sa.Column('short_faults', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_index('ix_micro_stop_buckets_tenant_bucket', 'micro_stop_buckets', ['tenant_id', 'bucket_start'])

    op.create_table(
        'machine_data_spans',
        sa.Column('id', UUID, primary_key=True), _tenant(),
        sa.Column('machine_id', UUID, sa.ForeignKey('machines.id', ondelete='CASCADE'), nullable=False),
        sa.Column('start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_machine_data_spans_tenant_id', 'machine_data_spans', ['tenant_id'])
    op.create_index('ix_machine_data_spans_machine_start', 'machine_data_spans', ['machine_id', 'start'])

    op.create_table(
        'downtime_detector_states',
        sa.Column('machine_id', UUID, sa.ForeignKey('machines.id', ondelete='CASCADE'), primary_key=True),
        _tenant(),
        sa.Column('checkpoint', sa.DateTime(timezone=True), nullable=False),
        sa.Column('covered_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('state', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts('updated_at'),
    )
    op.create_index('ix_downtime_detector_states_tenant_id', 'downtime_detector_states', ['tenant_id'])


def downgrade() -> None:
    op.drop_table('downtime_detector_states')
    op.drop_table('machine_data_spans')
    op.drop_table('micro_stop_buckets')
    op.drop_constraint('fk_downtime_events_failure_id', 'downtime_events', type_='foreignkey')
    op.drop_table('failure_records')
    op.drop_table('downtime_events')
    op.drop_table('reason_codes')
    op.drop_table('production_runs')
    op.drop_table('machine_products')
    op.drop_table('products')
    op.drop_table('calendar_exceptions')
    op.drop_table('shifts')
    op.drop_column('machines', 'fault_code_map')
    op.drop_column('machines', 'micro_stop_threshold_s')
    op.drop_index('uq_positions_counter_tag_id', table_name='positions')
