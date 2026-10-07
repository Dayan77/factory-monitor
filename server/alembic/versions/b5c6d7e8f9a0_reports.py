"""Relatórios exportáveis e agendados (Sprint 6, docs/reports.md): agendamentos, períodos despachados
(idempotência), execuções versionadas, arquivos, envios, descadastro, destinatários externos e metas por linha

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-10-07 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'b5c6d7e8f9a0'
down_revision = 'a4b5c6d7e8f9'
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=False)
TS = sa.DateTime(timezone=True)
TYPES = "('daily_production', 'shift', 'weekly_oee', 'monthly_maintenance', 'reliability_life')"


def _tenant():
    return sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False)


def _stamps():
    return [sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', TS, server_default=sa.text('now()'), nullable=False)]


def upgrade() -> None:
    op.create_table(
        'report_schedules',
        sa.Column('id', UUID, primary_key=True),
        _tenant(),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('report_type', sa.String(32), nullable=False),
        sa.Column('scope_type', sa.String(16), nullable=False),
        sa.Column('scope_id', UUID, nullable=True),
        sa.Column('params', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('formats', postgresql.ARRAY(sa.String(8)), nullable=False, server_default=sa.text("'{pdf}'::varchar[]")),
        sa.Column('deliver_at_local', sa.String(5), nullable=True),
        sa.Column('weekday', sa.Integer(), nullable=True),
        sa.Column('month_day', sa.Integer(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('recipients', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('created_by', UUID, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('paused_at', TS, nullable=True),
        *_stamps(),
        sa.CheckConstraint(f"report_type IN {TYPES}", name='ck_report_schedules_type'),
        sa.CheckConstraint("scope_type IN ('tenant', 'plant', 'line')", name='ck_report_schedules_scope'),
        sa.CheckConstraint("weekday IS NULL OR (weekday BETWEEN 0 AND 6)", name='ck_report_schedules_weekday'),
        sa.CheckConstraint("month_day IS NULL OR (month_day BETWEEN 1 AND 28)", name='ck_report_schedules_month_day'),
    )
    op.create_index('ix_report_schedules_tenant', 'report_schedules', ['tenant_id', 'enabled'])

    op.create_table(
        'report_dispatches',
        sa.Column('id', UUID, primary_key=True),
        _tenant(),
        sa.Column('schedule_id', UUID, sa.ForeignKey('report_schedules.id', ondelete='CASCADE'), nullable=False),
        sa.Column('period_key', sa.String(128), nullable=False),
        sa.Column('period', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('ready_at', TS, nullable=False),
        sa.Column('send_at', TS, nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('next_attempt_at', TS, nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('manual', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('requested_by', UUID, nullable=True),
        sa.Column('completed_at', TS, nullable=True),
        *_stamps(),
        sa.UniqueConstraint('schedule_id', 'period_key', name='uq_report_dispatches_schedule_period'),
        sa.CheckConstraint("status IN ('pending', 'sent', 'no_channel', 'failed')", name='ck_report_dispatches_status'),
    )
    op.create_index('ix_report_dispatches_due', 'report_dispatches', ['status', 'next_attempt_at'])
    op.create_index('ix_report_dispatches_tenant', 'report_dispatches', ['tenant_id', 'created_at'])

    op.create_table(
        'report_runs',
        sa.Column('id', UUID, primary_key=True),
        _tenant(),
        sa.Column('schedule_id', UUID, sa.ForeignKey('report_schedules.id', ondelete='SET NULL'), nullable=True),
        sa.Column('dispatch_id', UUID, sa.ForeignKey('report_dispatches.id', ondelete='SET NULL'), nullable=True),
        sa.Column('report_type', sa.String(32), nullable=False),
        sa.Column('scope_type', sa.String(16), nullable=False),
        sa.Column('scope_id', UUID, nullable=True),
        sa.Column('scope_name', sa.String(255), nullable=True),
        sa.Column('period_key', sa.String(128), nullable=False),
        sa.Column('period_start', TS, nullable=True),
        sa.Column('period_end', TS, nullable=True),
        sa.Column('period_label', sa.String(128), nullable=True),
        sa.Column('params', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('format', sa.String(8), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('supersedes_id', UUID, nullable=True),
        sa.Column('profile_key', sa.String(255), nullable=False, server_default=sa.text("''")),
        sa.Column('viewer_user_id', UUID, nullable=True),
        sa.Column('requested_by', UUID, nullable=True),
        sa.Column('status', sa.String(16), nullable=False, server_default=sa.text("'generating'")),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('generated_at', TS, nullable=True),
        sa.Column('data_until', TS, nullable=True),
        sa.Column('generator_version', sa.String(32), nullable=False, server_default=sa.text("''")),
        sa.Column('file_sha256', sa.String(64), nullable=True),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('filename', sa.String(255), nullable=True),
        sa.Column('content_type', sa.String(128), nullable=True),
        sa.Column('observations', sa.Text(), nullable=True),
        sa.Column('summary', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(f"report_type IN {TYPES}", name='ck_report_runs_type'),
        sa.CheckConstraint("format IN ('pdf', 'xlsx')", name='ck_report_runs_format'),
        sa.CheckConstraint("status IN ('generating', 'done', 'failed')", name='ck_report_runs_status'),
    )
    op.create_index('ix_report_runs_tenant_created', 'report_runs', ['tenant_id', 'created_at'])
    op.create_index('ix_report_runs_version', 'report_runs',
                    ['tenant_id', 'report_type', 'scope_id', 'period_key', 'format', 'profile_key'])
    op.create_index('ix_report_runs_schedule', 'report_runs', ['schedule_id', 'created_at'])

    op.create_table(
        'report_files',
        sa.Column('run_id', UUID, sa.ForeignKey('report_runs.id', ondelete='CASCADE'), primary_key=True),
        _tenant(),
        sa.Column('content', sa.LargeBinary(), nullable=False),
    )
    op.create_index('ix_report_files_tenant_id', 'report_files', ['tenant_id'])

    op.create_table(
        'report_deliveries',
        sa.Column('id', UUID, primary_key=True),
        _tenant(),
        sa.Column('dispatch_id', UUID, sa.ForeignKey('report_dispatches.id', ondelete='CASCADE'), nullable=True),
        sa.Column('schedule_id', UUID, sa.ForeignKey('report_schedules.id', ondelete='SET NULL'), nullable=True),
        sa.Column('run_ids', postgresql.ARRAY(UUID), nullable=False, server_default=sa.text("'{}'::uuid[]")),
        sa.Column('recipient_user_id', UUID, nullable=True),
        sa.Column('external_recipient_id', UUID, nullable=True),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('channel', sa.String(16), nullable=True),
        sa.Column('provider_id', sa.String(255), nullable=True),
        sa.Column('link_only', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('sent_at', TS, nullable=True),
        sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('sent', 'failed', 'unsubscribed', 'no_channel', 'bounced')",
                           name='ck_report_deliveries_status'),
    )
    op.create_index('ix_report_deliveries_tenant', 'report_deliveries', ['tenant_id', 'created_at'])
    op.create_index('ix_report_deliveries_dispatch', 'report_deliveries', ['dispatch_id'])

    op.create_table(
        'report_unsubscribes',
        sa.Column('id', UUID, primary_key=True),
        _tenant(),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('schedule_id', UUID, sa.ForeignKey('report_schedules.id', ondelete='CASCADE'), nullable=True),
        sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('uq_report_unsubscribes', 'report_unsubscribes', ['tenant_id', 'email', 'schedule_id'],
                    unique=True, postgresql_nulls_not_distinct=True)

    op.create_table(
        'report_settings',
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('external_recipients_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('no_channel_warned_on', sa.Date(), nullable=True),
        *_stamps(),
    )

    op.create_table(
        'report_external_recipients',
        sa.Column('id', UUID, primary_key=True),
        _tenant(),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('purpose', sa.String(500), nullable=False),
        sa.Column('legal_basis', sa.String(64), nullable=False),
        sa.Column('bounce_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('disabled_at', TS, nullable=True),
        sa.Column('created_by', UUID, nullable=True),
        *_stamps(),
        sa.UniqueConstraint('tenant_id', 'email', name='uq_report_external_recipients_email'),
    )

    op.create_table(
        'line_targets',
        sa.Column('id', UUID, primary_key=True),
        _tenant(),
        sa.Column('line_id', UUID, sa.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False),
        sa.Column('oee', sa.Float(precision=53), nullable=True),
        sa.Column('parts_per_shift', sa.Float(precision=53), nullable=True),
        sa.Column('valid_from', sa.Date(), nullable=False),
        sa.Column('created_by', UUID, nullable=True),
        *_stamps(),
        sa.UniqueConstraint('line_id', 'valid_from', name='uq_line_targets_line_valid_from'),
        sa.CheckConstraint("oee IS NULL OR (oee > 0 AND oee <= 1)", name='ck_line_targets_oee'),
        sa.CheckConstraint("parts_per_shift IS NULL OR parts_per_shift > 0", name='ck_line_targets_parts'),
    )
    op.create_index('ix_line_targets_tenant', 'line_targets', ['tenant_id', 'line_id'])


def downgrade() -> None:
    op.drop_index('ix_line_targets_tenant', table_name='line_targets')
    op.drop_table('line_targets')
    op.drop_table('report_external_recipients')
    op.drop_table('report_settings')
    op.drop_index('uq_report_unsubscribes', table_name='report_unsubscribes')
    op.drop_table('report_unsubscribes')
    op.drop_index('ix_report_deliveries_dispatch', table_name='report_deliveries')
    op.drop_index('ix_report_deliveries_tenant', table_name='report_deliveries')
    op.drop_table('report_deliveries')
    op.drop_index('ix_report_files_tenant_id', table_name='report_files')
    op.drop_table('report_files')
    op.drop_index('ix_report_runs_schedule', table_name='report_runs')
    op.drop_index('ix_report_runs_version', table_name='report_runs')
    op.drop_index('ix_report_runs_tenant_created', table_name='report_runs')
    op.drop_table('report_runs')
    op.drop_index('ix_report_dispatches_tenant', table_name='report_dispatches')
    op.drop_index('ix_report_dispatches_due', table_name='report_dispatches')
    op.drop_table('report_dispatches')
    op.drop_index('ix_report_schedules_tenant', table_name='report_schedules')
    op.drop_table('report_schedules')
