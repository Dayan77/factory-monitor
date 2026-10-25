"""Endurecimento de autenticação e privacidade (Sprint 7, backend B — docs/security.md, docs/privacy.md):
sessões com rotação de refresh token, bloqueio por força bruta, limites de taxa, histórico de senhas, redefinição
de senha, convites, MFA (TOTP + códigos de recuperação), política de segurança por tenant e anonimização (LGPD)

Revision ID: d7e8f9a0b1c2
Revises: b5c6d7e8f9a0
Create Date: 2026-10-07 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'd7e8f9a0b1c2'
down_revision = 'b5c6d7e8f9a0'
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=False)
TS = sa.DateTime(timezone=True)
NOW = sa.text('now()')


def _user(name='user_id', ondelete='CASCADE', nullable=False):
    return sa.Column(name, UUID, sa.ForeignKey('users.id', ondelete=ondelete), nullable=nullable)


def upgrade() -> None:
    op.add_column('users', sa.Column('mfa_secret_enc', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('mfa_pending_enc', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('mfa_enabled_at', TS, nullable=True))
    op.add_column('users', sa.Column('mfa_last_step', sa.BigInteger(), nullable=True))
    op.add_column('users', sa.Column('password_changed_at', TS, nullable=True))
    op.add_column('users', sa.Column('tokens_valid_after', TS, nullable=True))
    op.add_column('users', sa.Column('anonymized_at', TS, nullable=True))

    op.create_table(
        'auth_sessions',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        _user(),
        sa.Column('created_at', TS, server_default=NOW, nullable=False),
        sa.Column('last_used_at', TS, nullable=True),
        sa.Column('expires_at', TS, nullable=False),
        sa.Column('revoked_at', TS, nullable=True),
        sa.Column('revoked_reason', sa.String(32), nullable=True),
        sa.Column('ip_prefix', sa.String(64), nullable=True),
        sa.Column('user_agent', sa.String(300), nullable=True),
        sa.Column('mfa', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    op.create_index('ix_auth_sessions_user', 'auth_sessions', ['user_id', 'revoked_at'])

    op.create_table(
        'refresh_tokens',
        sa.Column('jti', sa.String(64), primary_key=True),
        sa.Column('session_id', UUID, sa.ForeignKey('auth_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('issued_at', TS, server_default=NOW, nullable=False),
        sa.Column('expires_at', TS, nullable=False),
        sa.Column('used_at', TS, nullable=True),
        sa.Column('replaced_by', sa.String(64), nullable=True),
    )
    op.create_index('ix_refresh_tokens_session', 'refresh_tokens', ['session_id'])

    op.create_table(
        'auth_throttle',
        sa.Column('key', sa.String(128), primary_key=True),
        sa.Column('failures', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('window_start', TS, nullable=False),
        sa.Column('locked_until', TS, nullable=True),
        sa.Column('lock_level', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', TS, server_default=NOW, nullable=False),
    )

    op.create_table(
        'rate_counters',
        sa.Column('key', sa.String(128), primary_key=True),
        sa.Column('window_start', TS, primary_key=True),
        sa.Column('count', sa.Integer(), nullable=False, server_default='0'),
    )

    op.create_table(
        'password_history',
        sa.Column('id', UUID, primary_key=True),
        _user(),
        sa.Column('password_hash', sa.String(), nullable=False),
        sa.Column('created_at', TS, server_default=NOW, nullable=False),
    )
    op.create_index('ix_password_history_user', 'password_history', ['user_id', 'created_at'])

    op.create_table(
        'password_reset_tokens',
        sa.Column('id', UUID, primary_key=True),
        _user(),
        sa.Column('created_at', TS, server_default=NOW, nullable=False),
        sa.Column('expires_at', TS, nullable=False),
        sa.Column('used_at', TS, nullable=True),
    )
    op.create_index('ix_password_reset_tokens_user', 'password_reset_tokens', ['user_id'])

    op.create_table(
        'user_invitations',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('role', sa.String(32), nullable=False),
        sa.Column('roles', postgresql.ARRAY(sa.String(32)), nullable=False, server_default=sa.text("'{}'::varchar[]")),
        sa.Column('line_ids', postgresql.ARRAY(UUID), nullable=False, server_default=sa.text("'{}'::uuid[]")),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        _user('invited_by', 'SET NULL', True),
        _user('user_id', 'SET NULL', True),
        sa.Column('created_at', TS, server_default=NOW, nullable=False),
        sa.Column('expires_at', TS, nullable=False),
        sa.Column('accepted_at', TS, nullable=True),
        sa.Column('revoked_at', TS, nullable=True),
    )
    op.create_index('ix_user_invitations_tenant', 'user_invitations', ['tenant_id', 'created_at'])

    op.create_table(
        'mfa_recovery_codes',
        sa.Column('id', UUID, primary_key=True),
        _user(),
        sa.Column('code_hash', sa.String(64), nullable=False),
        sa.Column('created_at', TS, server_default=NOW, nullable=False),
        sa.Column('used_at', TS, nullable=True),
    )
    op.create_index('ix_mfa_recovery_codes_user', 'mfa_recovery_codes', ['user_id'])

    op.create_table(
        'tenant_security_settings',
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('require_mfa_admins', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        _user('updated_by', 'SET NULL', True),
        sa.Column('updated_at', TS, server_default=NOW, nullable=False),
    )
    # audit trail is read per tenant by time and, for the LGPD export, per user
    op.create_index('ix_audit_events_user', 'audit_events', ['user_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_audit_events_user', table_name='audit_events')
    op.drop_table('tenant_security_settings')
    op.drop_index('ix_mfa_recovery_codes_user', table_name='mfa_recovery_codes')
    op.drop_table('mfa_recovery_codes')
    op.drop_index('ix_user_invitations_tenant', table_name='user_invitations')
    op.drop_table('user_invitations')
    op.drop_index('ix_password_reset_tokens_user', table_name='password_reset_tokens')
    op.drop_table('password_reset_tokens')
    op.drop_index('ix_password_history_user', table_name='password_history')
    op.drop_table('password_history')
    op.drop_table('rate_counters')
    op.drop_table('auth_throttle')
    op.drop_index('ix_refresh_tokens_session', table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
    op.drop_index('ix_auth_sessions_user', table_name='auth_sessions')
    op.drop_table('auth_sessions')
    for col in ('anonymized_at', 'tokens_valid_after', 'password_changed_at', 'mfa_last_step', 'mfa_enabled_at',
                'mfa_pending_enc', 'mfa_secret_enc'):
        op.drop_column('users', col)
