"""Fase 3 (Sprint 4): manutenção — papéis, planos, OS, falhas ISO 14224 completas, notificações,
Web Push, tokens de API e webhooks

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-10-05 12:00:00.000000

* userrole += maintenance_planner, technician, production_supervisor; users.roles[] (vários papéis,
  `role` continua como papel principal), users.line_ids[], users.on_call
* maintenance_plans, work_orders (+ history, labor, replacements), maintenance_settings, maintenance_absences
* failure_records: component_id, work_order_id, detected_at, repair_started_at, repair_finished_at,
  usage_at_failure, note, emergency_notified_at, escalated_at
* notifications, notification_preferences, push_subscriptions, api_tokens, webhooks, webhook_deliveries
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'b9c0d1e2f3a4'
down_revision = 'a8b9c0d1e2f3'
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=False)
NEW_ROLES = ("maintenance_planner", "technician", "production_supervisor")
OLD_ROLES = ("operator", "manager", "admin", "superadmin")
OPEN_STATES = "('draft','open','approved','scheduled','in_progress','on_hold')"


def _ts(name, nullable=False, default=True):
    kw = {'server_default': sa.text('now()')} if default else {}
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, **kw)


def _tenant(index=False):
    return sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=index)


def _user_fk(name, ondelete='SET NULL', nullable=True):
    return sa.Column(name, UUID, sa.ForeignKey('users.id', ondelete=ondelete), nullable=nullable)


def upgrade() -> None:
    # ── papéis ────────────────────────────────────────────────────────────
    for r in NEW_ROLES:
        op.execute(f"ALTER TYPE userrole ADD VALUE IF NOT EXISTS '{r}'")
    op.add_column('users', sa.Column('roles', postgresql.ARRAY(sa.String(32)), nullable=False,
                                     server_default=sa.text("'{}'::varchar[]")))
    op.add_column('users', sa.Column('line_ids', postgresql.ARRAY(UUID), nullable=False,
                                     server_default=sa.text("'{}'::uuid[]")))
    op.add_column('users', sa.Column('on_call', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE users SET roles = ARRAY[role::text]::varchar[]")

    # ── planos e OS ───────────────────────────────────────────────────────
    op.create_table(
        'maintenance_plans',
        sa.Column('id', UUID, primary_key=True), _tenant(),
        sa.Column('position_id', UUID, sa.ForeignKey('positions.id', ondelete='CASCADE'), nullable=True, index=True),
        sa.Column('machine_id', UUID, sa.ForeignKey('machines.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('task_list', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('estimated_hours', sa.Float(), nullable=False, server_default='0'),
        sa.Column('wo_type', sa.String(16), nullable=False, server_default='preventive'),
        sa.Column('interval_cycles', sa.Float(precision=53), nullable=True),
        sa.Column('interval_hours', sa.Float(precision=53), nullable=True),
        sa.Column('interval_starts', sa.Float(precision=53), nullable=True),
        sa.Column('interval_days', sa.Integer(), nullable=True),
        sa.Column('tolerance_pct', sa.Float(), nullable=False, server_default='10'),
        sa.Column('reset_on_replacement', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('stops_machine', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('last_reset_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_reset_usage', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('last_reset_by', sa.String(255), nullable=True),
        sa.Column('state', sa.String(16), nullable=False, server_default='ok'),
        sa.Column('consumed', sa.Float(precision=53), nullable=True),
        sa.Column('evaluated_at', sa.DateTime(timezone=True), nullable=True),
        _user_fk('created_by'),
        _ts('created_at'), _ts('updated_at'),
        sa.CheckConstraint("wo_type IN ('preventive', 'predictive')", name='ck_maintenance_plans_wo_type'),
        sa.CheckConstraint("state IN ('ok', 'due_soon', 'due', 'overdue')", name='ck_maintenance_plans_state'),
        sa.CheckConstraint("position_id IS NOT NULL OR machine_id IS NOT NULL", name='ck_maintenance_plans_anchor'),
    )
    op.create_index('ix_maintenance_plans_tenant', 'maintenance_plans', ['tenant_id', 'is_active'])

    op.create_table(
        'work_orders',
        sa.Column('id', UUID, primary_key=True), _tenant(),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(16), nullable=False),
        sa.Column('urgency', sa.String(16), nullable=True),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('plan_id', UUID, sa.ForeignKey('maintenance_plans.id', ondelete='SET NULL'), nullable=True, index=True),
        sa.Column('position_id', UUID, sa.ForeignKey('positions.id', ondelete='SET NULL'), nullable=True, index=True),
        sa.Column('machine_id', UUID, sa.ForeignKey('machines.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('downtime_event_id', UUID, sa.ForeignKey('downtime_events.id', ondelete='SET NULL'), nullable=True),
        sa.Column('failure_id', UUID, sa.ForeignKey('failure_records.id', ondelete='SET NULL'), nullable=True),
        sa.Column('parent_id', UUID, sa.ForeignKey('work_orders.id', ondelete='SET NULL'), nullable=True),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('task_list', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('estimated_hours', sa.Float(), nullable=False, server_default='0'),
        sa.Column('labor_hours', sa.Float(), nullable=False, server_default='0'),
        sa.Column('remaining_hours', sa.Float(), nullable=True),
        sa.Column('stops_machine', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('scheduled_for', sa.Date(), nullable=True),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('assignee_id', UUID, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True),
        _user_fk('created_by'),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancel_reason', sa.String(32), nullable=True),
        sa.Column('hold_reason', sa.String(500), nullable=True),
        sa.Column('state_changed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('overdue_notified_on', sa.Date(), nullable=True),
        _ts('created_at'), _ts('updated_at'),
        sa.UniqueConstraint('tenant_id', 'number', name='uq_work_orders_tenant_number'),
        sa.CheckConstraint("type IN ('preventive', 'predictive', 'corrective', 'improvement')", name='ck_work_orders_type'),
        sa.CheckConstraint("state IN ('draft', 'open', 'approved', 'scheduled', 'in_progress', 'on_hold', 'completed', "
                           "'closed', 'cancelled')", name='ck_work_orders_state'),
        sa.CheckConstraint("urgency IS NULL OR urgency IN ('emergency', 'deferred')", name='ck_work_orders_urgency'),
        sa.CheckConstraint("cancel_reason IS NULL OR cancel_reason IN ('superseded', 'production_conflict', 'duplicate', "
                           "'not_needed', 'other')", name='ck_work_orders_cancel_reason'),
    )
    op.create_index('ix_work_orders_tenant_state', 'work_orders', ['tenant_id', 'state'])
    op.create_index('uq_work_orders_one_open_per_plan', 'work_orders', ['plan_id'], unique=True,
                    postgresql_where=sa.text(f"plan_id IS NOT NULL AND state IN {OPEN_STATES}"))

    op.create_table(
        'work_order_history',
        sa.Column('id', UUID, primary_key=True), _tenant(index=True),
        sa.Column('work_order_id', UUID, sa.ForeignKey('work_orders.id', ondelete='CASCADE'), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        _user_fk('by'),
        sa.Column('from_state', sa.String(16), nullable=True),
        sa.Column('to_state', sa.String(16), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        _ts('recorded_at'),
    )
    op.create_index('ix_work_order_history_wo_at', 'work_order_history', ['work_order_id', 'at'])

    op.create_table(
        'work_order_labor',
        sa.Column('id', UUID, primary_key=True), _tenant(index=True),
        sa.Column('work_order_id', UUID, sa.ForeignKey('work_orders.id', ondelete='CASCADE'), nullable=False, index=True),
        _user_fk('user_id'),
        sa.Column('hours', sa.Float(), nullable=False),
        sa.Column('remaining_hours', sa.Float(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('client_action_id', sa.String(64), nullable=True),
        sa.UniqueConstraint('work_order_id', 'client_action_id', name='uq_work_order_labor_client_action'),
    )

    op.create_table(
        'work_order_replacements',
        sa.Column('id', UUID, primary_key=True), _tenant(index=True),
        sa.Column('work_order_id', UUID, sa.ForeignKey('work_orders.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('position_id', UUID, sa.ForeignKey('positions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('removed_component_id', UUID, sa.ForeignKey('components.id', ondelete='SET NULL'), nullable=True),
        sa.Column('installed_component_id', UUID, sa.ForeignKey('components.id', ondelete='CASCADE'), nullable=False),
        sa.Column('removal_reason', sa.String(32), nullable=False),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        _user_fk('created_by'),
        _ts('created_at'),
    )

    op.create_table(
        'maintenance_settings',
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('schedule_tolerance_days', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('technician_hours_per_week', sa.Float(), nullable=False, server_default='44'),
        sa.Column('due_soon_at', sa.Float(), nullable=False, server_default='0.8'),
        sa.Column('emergency_call_after_s', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('default_mttr_min', sa.Float(), nullable=False, server_default='60'),
        sa.Column('digest_hour_local', sa.Integer(), nullable=False, server_default='7'),
        _ts('created_at'), _ts('updated_at'),
    )
    op.create_table(
        'maintenance_absences',
        sa.Column('id', UUID, primary_key=True), _tenant(index=True),
        _user_fk('user_id', ondelete='CASCADE', nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('hours', sa.Float(), nullable=False),
        sa.Column('note', sa.String(255), nullable=True),
        _ts('created_at'), _ts('updated_at'),
    )

    # ── falhas ISO 14224 completas ────────────────────────────────────────
    op.add_column('failure_records', sa.Column('component_id', UUID, sa.ForeignKey('components.id', ondelete='SET NULL'), nullable=True))
    op.create_index('ix_failure_records_component_id', 'failure_records', ['component_id'])
    op.add_column('failure_records', sa.Column('work_order_id', UUID, nullable=True))
    op.create_foreign_key('fk_failure_records_work_order_id', 'failure_records', 'work_orders',
                          ['work_order_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_failure_records_work_order_id', 'failure_records', ['work_order_id'])
    for col in ('detected_at', 'repair_started_at', 'repair_finished_at', 'emergency_notified_at', 'escalated_at'):
        op.add_column('failure_records', sa.Column(col, sa.DateTime(timezone=True), nullable=True))
    op.add_column('failure_records', sa.Column('usage_at_failure', postgresql.JSONB(), nullable=True))
    op.add_column('failure_records', sa.Column('note', sa.Text(), nullable=True))
    # rascunhos antigos (Sprint 3) já notificados: não disparar chamado emergencial retroativo
    op.execute("UPDATE failure_records SET emergency_notified_at = now()")

    # ── notificações, push, integração ───────────────────────────────────
    op.create_table(
        'notifications',
        sa.Column('id', UUID, primary_key=True), _tenant(),
        _user_fk('user_id', ondelete='CASCADE', nullable=False),
        sa.Column('kind', sa.String(48), nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('link', sa.String(500), nullable=True),
        sa.Column('priority', sa.String(16), nullable=False, server_default='normal'),
        sa.Column('data', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('group_key', sa.String(255), nullable=True),
        sa.Column('count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('line_id', UUID, nullable=True),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('emailed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('pushed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('digest', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('digested_at', sa.DateTime(timezone=True), nullable=True),
        _ts('created_at'), _ts('updated_at'),
    )
    op.create_index('ix_notifications_user_unread', 'notifications', ['user_id', 'read_at', 'created_at'])
    op.create_index('ix_notifications_tenant_created', 'notifications', ['tenant_id', 'created_at'])
    op.create_table(
        'notification_preferences',
        sa.Column('user_id', UUID, sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        _tenant(index=True),
        sa.Column('channels', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('digest_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('last_digest_on', sa.Date(), nullable=True),
        _ts('created_at'), _ts('updated_at'),
    )
    op.create_table(
        'push_subscriptions',
        sa.Column('id', UUID, primary_key=True), _tenant(index=True),
        _user_fk('user_id', ondelete='CASCADE', nullable=False),
        sa.Column('endpoint', sa.Text(), nullable=False, unique=True),
        sa.Column('p256dh', sa.String(255), nullable=False),
        sa.Column('auth', sa.String(255), nullable=False),
        sa.Column('user_agent', sa.String(255), nullable=True),
        sa.Column('last_error', sa.String(500), nullable=True),
        _ts('created_at'), _ts('updated_at'),
    )
    op.create_index('ix_push_subscriptions_user_id', 'push_subscriptions', ['user_id'])
    op.create_table(
        'api_tokens',
        sa.Column('id', UUID, primary_key=True), _tenant(index=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('prefix', sa.String(16), nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('scopes', postgresql.ARRAY(sa.String(16)), nullable=False),
        _user_fk('created_by'),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        _ts('created_at'), _ts('updated_at'),
    )
    op.create_table(
        'webhooks',
        sa.Column('id', UUID, primary_key=True), _tenant(index=True),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('secret', sa.String(255), nullable=False),
        sa.Column('events', postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column('description', sa.String(255), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        _user_fk('created_by'),
        _ts('created_at'), _ts('updated_at'),
    )
    op.create_table(
        'webhook_deliveries',
        sa.Column('id', UUID, primary_key=True), _tenant(index=True),
        sa.Column('webhook_id', UUID, sa.ForeignKey('webhooks.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('event', sa.String(64), nullable=False),
        sa.Column('payload', postgresql.JSONB(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_status_code', sa.Integer(), nullable=True),
        sa.Column('last_error', sa.String(500), nullable=True),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        _ts('created_at'),
    )
    op.create_index('ix_webhook_deliveries_pending', 'webhook_deliveries', ['status', 'next_attempt_at'])


def downgrade() -> None:
    for t in ('webhook_deliveries', 'webhooks', 'api_tokens', 'push_subscriptions', 'notification_preferences',
              'notifications'):
        op.drop_table(t)

    op.drop_index('ix_failure_records_work_order_id', table_name='failure_records')
    op.drop_constraint('fk_failure_records_work_order_id', 'failure_records', type_='foreignkey')
    op.drop_index('ix_failure_records_component_id', table_name='failure_records')
    for col in ('component_id', 'work_order_id', 'detected_at', 'repair_started_at', 'repair_finished_at',
                'emergency_notified_at', 'escalated_at', 'usage_at_failure', 'note'):
        op.drop_column('failure_records', col)

    for t in ('maintenance_absences', 'maintenance_settings', 'work_order_replacements', 'work_order_labor',
              'work_order_history', 'work_orders', 'maintenance_plans'):
        op.drop_table(t)

    op.drop_column('users', 'on_call')
    op.drop_column('users', 'line_ids')
    op.drop_column('users', 'roles')
    # Postgres não remove valores de enum: recria o tipo sem os papéis novos.
    op.execute(f"UPDATE users SET role = 'operator' WHERE role::text IN {NEW_ROLES}")
    op.execute("ALTER TYPE userrole RENAME TO userrole_old")
    op.execute(f"CREATE TYPE userrole AS ENUM {OLD_ROLES}")
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE userrole USING role::text::userrole")
    op.execute("DROP TYPE userrole_old")
