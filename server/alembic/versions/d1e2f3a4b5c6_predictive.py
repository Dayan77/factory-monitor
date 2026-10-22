"""Manutenção preditiva (Sprint 5, docs/predictive.md P-1…P-12)

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-10-05 20:00:00.000000

* predictive_monitors / predictive_rules: sinal monitorado por (posição, sinal) e regras de condição (P-1, P-3).
* condition_baselines: eventos de reinício e linhas de base (P-2), nunca apagados.
* condition_features: uma característica por janela + resultado dos detectores (P-1, P-4…P-6, P-12).
* predictive_alerts (+ predictive_alert_events): alerta único por (posição, sinal) não fechado e sua trilha (P-8).
* production_plan_days: plano diário de produção por máquina (P-7).
* work_orders.source_alert_id / finding / resets_baseline / due_by; maintenance_plans.condition_trigger /
  condition_due_date / condition_alert_id (P-9).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'd1e2f3a4b5c6'
down_revision = 'c0d1e2f3a4b5'
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=False)
JSONB = postgresql.JSONB


def _ts(name, nullable=True, **kw):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, **kw)


def upgrade() -> None:
    op.create_table(
        'predictive_monitors',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('position_id', UUID, sa.ForeignKey('positions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('machine_id', UUID, sa.ForeignKey('machines.id', ondelete='CASCADE'), nullable=False),
        sa.Column('feature', sa.String(16), nullable=False),
        sa.Column('tag_id', UUID, sa.ForeignKey('tags.id', ondelete='SET NULL'), nullable=True),
        sa.Column('ambient_tag_id', UUID, sa.ForeignKey('tags.id', ondelete='SET NULL'), nullable=True),
        sa.Column('direction', sa.String(8), nullable=False, server_default=sa.text("'up'")),
        sa.Column('window_cycles', sa.Integer, nullable=True),
        sa.Column('window_hours', sa.Float, nullable=True),
        _ts('anchor_at', nullable=False),
        sa.Column('warmup_windows', sa.Integer, nullable=False),
        sa.Column('failure_limit', sa.Float(precision=53), nullable=True),
        sa.Column('failure_limit_relative', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('sensor_resolution', sa.Float, nullable=False, server_default=sa.text('0')),
        sa.Column('unit', sa.String(32), nullable=True),
        sa.Column('params', JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('enabled', sa.Boolean, nullable=False, server_default=sa.text('true')),
        sa.Column('status', JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts('processed_until'),
        sa.Column('created_by', UUID, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        _ts('created_at', nullable=False, server_default=sa.func.now()),
        _ts('updated_at', nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('position_id', 'feature', name='uq_predictive_monitors_position_feature'),
        sa.CheckConstraint("feature IN ('stroke_time', 'current', 'vibration', 'temperature')",
                           name='ck_predictive_monitors_feature'),
        sa.CheckConstraint("direction IN ('up', 'down')", name='ck_predictive_monitors_direction'),
        sa.CheckConstraint('(window_cycles IS NOT NULL) <> (window_hours IS NOT NULL)',
                           name='ck_predictive_monitors_window'),
    )
    op.create_index('ix_predictive_monitors_tenant', 'predictive_monitors', ['tenant_id', 'enabled'])
    op.create_index('ix_predictive_monitors_position_id', 'predictive_monitors', ['position_id'])
    op.create_index('ix_predictive_monitors_machine_id', 'predictive_monitors', ['machine_id'])

    op.create_table(
        'predictive_rules',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('monitor_id', UUID, sa.ForeignKey('predictive_monitors.id', ondelete='CASCADE'), nullable=False),
        sa.Column('code', sa.String(64), nullable=False),
        sa.Column('type', sa.String(24), nullable=False),
        sa.Column('value', sa.Float(precision=53), nullable=False),
        sa.Column('sustained_for', sa.Integer, nullable=False, server_default=sa.text('2')),
        sa.Column('severity', sa.String(16), nullable=False),
        sa.Column('enabled', sa.Boolean, nullable=False, server_default=sa.text('true')),
        sa.Column('description', sa.String(255), nullable=True),
        _ts('created_at', nullable=False, server_default=sa.func.now()),
        _ts('updated_at', nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('monitor_id', 'code', name='uq_predictive_rules_monitor_code'),
        sa.CheckConstraint("type IN ('threshold', 'pct_of_baseline', 'rate_of_change')",
                           name='ck_predictive_rules_type'),
        sa.CheckConstraint("severity IN ('watch', 'warning', 'critical')", name='ck_predictive_rules_severity'),
        sa.CheckConstraint('sustained_for >= 1', name='ck_predictive_rules_sustained'),
    )
    op.create_index('ix_predictive_rules_tenant_id', 'predictive_rules', ['tenant_id'])
    op.create_index('ix_predictive_rules_monitor_id', 'predictive_rules', ['monitor_id'])

    op.create_table(
        'condition_baselines',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('monitor_id', UUID, sa.ForeignKey('predictive_monitors.id', ondelete='CASCADE'), nullable=False),
        sa.Column('position_id', UUID, sa.ForeignKey('positions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('feature', sa.String(16), nullable=False),
        _ts('reset_at', nullable=False),
        sa.Column('reset_kind', sa.String(24), nullable=False),
        sa.Column('warmup', sa.Boolean, nullable=False),
        sa.Column('reason', sa.Text, nullable=True),
        sa.Column('source_id', sa.String(64), nullable=True),
        sa.Column('user_id', UUID, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('first_i', sa.Integer, nullable=True),
        sa.Column('m0', sa.Float(precision=53), nullable=True),
        sa.Column('mad', sa.Float(precision=53), nullable=True),
        sa.Column('sigma', sa.Float(precision=53), nullable=True),
        sa.Column('sigma_floor', sa.Float(precision=53), nullable=True),
        sa.Column('from_i', sa.Integer, nullable=True),
        sa.Column('to_i', sa.Integer, nullable=True),
        sa.Column('n', sa.Integer, nullable=True),
        _ts('established_at'),
        _ts('created_at', nullable=False, server_default=sa.text('now()')),
        sa.CheckConstraint("reset_kind IN ('initial', 'installation', 'work_order', 'manual', 'alert_rebaseline', "
                           "'config_change')", name='ck_condition_baselines_kind'),
    )
    op.create_index('ix_condition_baselines_tenant_id', 'condition_baselines', ['tenant_id'])
    op.create_index('ix_condition_baselines_monitor_reset', 'condition_baselines', ['monitor_id', 'reset_at'])

    op.create_table(
        'condition_features',
        sa.Column('monitor_id', UUID, sa.ForeignKey('predictive_monitors.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('i', sa.Integer, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('position_id', UUID, sa.ForeignKey('positions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('feature', sa.String(16), nullable=False),
        sa.Column('baseline_id', UUID, sa.ForeignKey('condition_baselines.id', ondelete='SET NULL'), nullable=True),
        _ts('start_at', nullable=False),
        _ts('end_at', nullable=False),
        sa.Column('x', sa.Float(precision=53), nullable=True),
        sa.Column('n', sa.Integer, nullable=False, server_default=sa.text('0')),
        sa.Column('valid', sa.Boolean, nullable=False),
        sa.Column('u', sa.Float(precision=53), nullable=False, server_default=sa.text('0')),
        sa.Column('running_h', sa.Float, nullable=True),
        sa.Column('coverage', sa.Float, nullable=True),
        sa.Column('phase', sa.String(16), nullable=True),
        sa.Column('z', sa.Float(precision=53), nullable=True),
        sa.Column('ewma', sa.Float(precision=53), nullable=True),
        sa.Column('cusum', sa.Float(precision=53), nullable=True),
        sa.Column('rolling_z', sa.Float(precision=53), nullable=True),
        sa.Column('anomaly', sa.Boolean, nullable=True),
        sa.Column('slope', sa.Float(precision=53), nullable=True),
        sa.Column('triggers', JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('severity', sa.String(16), nullable=True),
        sa.Column('rul', JSONB, nullable=True),
        _ts('created_at', nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_condition_features_tenant_pos_feat_end', 'condition_features',
                    ['tenant_id', 'position_id', 'feature', 'end_at'])

    op.create_table(
        'predictive_alerts',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('monitor_id', UUID, sa.ForeignKey('predictive_monitors.id', ondelete='CASCADE'), nullable=False),
        sa.Column('baseline_id', UUID, sa.ForeignKey('condition_baselines.id', ondelete='SET NULL'), nullable=True),
        sa.Column('position_id', UUID, sa.ForeignKey('positions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('machine_id', UUID, sa.ForeignKey('machines.id', ondelete='CASCADE'), nullable=False),
        sa.Column('feature', sa.String(16), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('severity', sa.String(16), nullable=False),
        _ts('opened_at', nullable=False),
        sa.Column('opened_window', sa.Integer, nullable=True),
        sa.Column('first_triggers', JSONB, nullable=False),
        sa.Column('escalations', JSONB, nullable=False),
        sa.Column('escalation_windows', JSONB, nullable=False),
        sa.Column('triggers_seen', JSONB, nullable=False),
        sa.Column('evidence', JSONB, nullable=False),
        sa.Column('explanation', sa.Text, nullable=True),
        sa.Column('rul', JSONB, nullable=True),
        sa.Column('last_point', JSONB, nullable=True),
        sa.Column('acknowledged', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('work_order_id', UUID, nullable=True),
        sa.Column('resolution', sa.String(24), nullable=True),
        _ts('closed_at'),
        sa.Column('closed_by', UUID, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        _ts('created_at', nullable=False, server_default=sa.func.now()),
        _ts('updated_at', nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('monitor_id', 'baseline_id', 'opened_window', name='uq_predictive_alerts_segment_window'),
        sa.CheckConstraint("state IN ('open', 'acknowledged', 'converted', 'closed')", name='ck_predictive_alerts_state'),
        sa.CheckConstraint("severity IN ('watch', 'warning', 'critical')", name='ck_predictive_alerts_severity'),
        sa.CheckConstraint("resolution IS NULL OR resolution IN ('confirmed', 'false_alarm', 'failed_before_action', "
                           "'rebaseline', 'duplicate', 'known_cause', 'auto_cleared')",
                           name='ck_predictive_alerts_resolution'),
    )
    op.create_foreign_key('fk_predictive_alerts_work_order', 'predictive_alerts', 'work_orders',
                          ['work_order_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_predictive_alerts_monitor_id', 'predictive_alerts', ['monitor_id'])
    op.create_index('ix_predictive_alerts_tenant_state', 'predictive_alerts', ['tenant_id', 'state', 'severity'])
    op.create_index('uq_predictive_alerts_one_open', 'predictive_alerts', ['position_id', 'feature'], unique=True,
                    postgresql_where=sa.text("state <> 'closed'"))

    op.create_table(
        'predictive_alert_events',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('alert_id', UUID, sa.ForeignKey('predictive_alerts.id', ondelete='CASCADE'), nullable=False),
        _ts('at', nullable=False),
        sa.Column('action', sa.String(24), nullable=False),
        sa.Column('source', sa.String(16), nullable=False),
        sa.Column('from_state', sa.String(16), nullable=True),
        sa.Column('to_state', sa.String(16), nullable=True),
        sa.Column('severity', sa.String(16), nullable=True),
        sa.Column('resolution', sa.String(24), nullable=True),
        sa.Column('comment', sa.Text, nullable=True),
        sa.Column('by', UUID, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('work_order_id', UUID, sa.ForeignKey('work_orders.id', ondelete='SET NULL'), nullable=True),
        sa.Column('window_i', sa.Integer, nullable=True),
        _ts('recorded_at', nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_predictive_alert_events_tenant_id', 'predictive_alert_events', ['tenant_id'])
    op.create_index('ix_predictive_alert_events_alert_at', 'predictive_alert_events', ['alert_id', 'at'])

    op.create_table(
        'production_plan_days',
        sa.Column('machine_id', UUID, sa.ForeignKey('machines.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('day', sa.Date, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('planned_parts', sa.Float, nullable=True),
        sa.Column('planned_hours', sa.Float, nullable=True),
        _ts('created_at', nullable=False, server_default=sa.func.now()),
        _ts('updated_at', nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_production_plan_days_tenant_id', 'production_plan_days', ['tenant_id'])

    op.add_column('work_orders', sa.Column('source_alert_id', UUID,
                                           sa.ForeignKey('predictive_alerts.id', ondelete='SET NULL',
                                                         name='fk_work_orders_source_alert'), nullable=True))
    op.add_column('work_orders', sa.Column('finding', sa.String(24), nullable=True))
    op.add_column('work_orders', sa.Column('resets_baseline', sa.Boolean, nullable=True))
    op.add_column('work_orders', sa.Column('due_by', sa.Date, nullable=True))
    op.create_index('ix_work_orders_source_alert_id', 'work_orders', ['source_alert_id'])
    op.create_check_constraint('ck_work_orders_finding', 'work_orders',
                               "finding IS NULL OR finding IN ('defect_confirmed', 'no_defect')")
    op.add_column('maintenance_plans', sa.Column('condition_trigger', JSONB, nullable=True))
    op.add_column('maintenance_plans', sa.Column('condition_due_date', sa.Date, nullable=True))
    op.add_column('maintenance_plans', sa.Column('condition_alert_id', UUID, nullable=True))


def downgrade() -> None:
    op.drop_column('maintenance_plans', 'condition_alert_id')
    op.drop_column('maintenance_plans', 'condition_due_date')
    op.drop_column('maintenance_plans', 'condition_trigger')
    op.drop_constraint('ck_work_orders_finding', 'work_orders', type_='check')
    op.drop_index('ix_work_orders_source_alert_id', table_name='work_orders')
    op.drop_column('work_orders', 'due_by')
    op.drop_column('work_orders', 'resets_baseline')
    op.drop_column('work_orders', 'finding')
    op.drop_column('work_orders', 'source_alert_id')
    op.drop_table('production_plan_days')
    op.drop_table('predictive_alert_events')
    op.drop_constraint('fk_predictive_alerts_work_order', 'predictive_alerts', type_='foreignkey')
    op.drop_table('predictive_alerts')
    op.drop_table('condition_features')
    op.drop_table('condition_baselines')
    op.drop_table('predictive_rules')
    op.drop_table('predictive_monitors')
