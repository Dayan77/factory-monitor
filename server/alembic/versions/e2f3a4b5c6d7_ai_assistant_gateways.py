"""Assistente de IA (conversas, auditoria, consumo) e gateways de campo (frota, cadastro, config remota)

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-10-05 20:00:00.000000

* gateways: um por dispositivo de campo (tenant, planta, hash do token, código de cadastro 24 h, heartbeat,
  versão da configuração, usuário MQTT);
* tags.gateway_id (FK gateways, SET NULL) + tags.edge (JSONB: campos do mapa v1 do edge);
* ai_conversations / ai_messages (conversas por usuário), ai_tool_calls (auditoria), ai_usage (tokens/dia).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'e2f3a4b5c6d7'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=False)
JSONB = postgresql.JSONB()
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'gateways',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plant_id', UUID, sa.ForeignKey('plants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('description', sa.String(500), nullable=True),
        sa.Column('token_hash', sa.String(64), nullable=True, unique=True),
        sa.Column('token_prefix', sa.String(16), nullable=True),
        sa.Column('enrolled_at', TS, nullable=True),
        sa.Column('enroll_code_hash', sa.String(64), nullable=True, unique=True),
        sa.Column('enroll_expires_at', TS, nullable=True),
        sa.Column('last_seen_at', TS, nullable=True),
        sa.Column('version', sa.String(64), nullable=True),
        sa.Column('status', sa.String(16), nullable=False, server_default='pending'),
        sa.Column('last_heartbeat', JSONB, nullable=True),
        sa.Column('heartbeat_s', sa.Float(), nullable=False, server_default='10'),
        sa.Column('pulse_poll_ms', sa.Integer(), nullable=False, server_default='20'),
        sa.Column('devices', JSONB, nullable=False, server_default='[]'),
        sa.Column('config_version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('config_hash', sa.String(64), nullable=True),
        sa.Column('config_fetched_at', TS, nullable=True),
        sa.Column('mqtt_username', sa.String(64), nullable=True, unique=True),
        sa.Column('mqtt_password_hash', sa.String(255), nullable=True),
        sa.Column('created_at', TS, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending', 'online', 'degraded', 'offline')", name='ck_gateways_status'),
    )
    op.create_index('ix_gateways_tenant_id', 'gateways', ['tenant_id'])
    op.create_index('ix_gateways_tenant_plant', 'gateways', ['tenant_id', 'plant_id'])
    op.add_column('tags', sa.Column('gateway_id', UUID, sa.ForeignKey('gateways.id', ondelete='SET NULL'),
                                    nullable=True))
    op.add_column('tags', sa.Column('edge', JSONB, nullable=True))
    op.create_index('ix_tags_gateway_id', 'tags', ['gateway_id'])

    op.create_table(
        'ai_conversations',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', UUID, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('created_at', TS, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_ai_conversations_tenant_user', 'ai_conversations', ['tenant_id', 'user_id', 'updated_at'])
    op.create_table(
        'ai_messages',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('conversation_id', UUID, sa.ForeignKey('ai_conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(16), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('citations', JSONB, nullable=True),
        sa.Column('tool_calls', JSONB, nullable=True),
        sa.Column('warnings', JSONB, nullable=True),
        sa.Column('verified', sa.Boolean(), nullable=True),
        sa.Column('refused', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('refusal_reason', sa.String(32), nullable=True),
        sa.Column('context', JSONB, nullable=True),
        sa.Column('created_at', TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_ai_messages_conversation', 'ai_messages', ['conversation_id', 'created_at'])
    op.create_table(
        'ai_tool_calls',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', UUID, sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('conversation_id', UUID, sa.ForeignKey('ai_conversations.id', ondelete='SET NULL'), nullable=True),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('args', JSONB, nullable=True),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('http_status', sa.Integer(), nullable=True),
        sa.Column('rows', sa.Integer(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_ai_tool_calls_tenant_created', 'ai_tool_calls', ['tenant_id', 'created_at'])
    op.create_table(
        'ai_usage',
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('day', sa.Date(), primary_key=True),
        sa.Column('input_tokens', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('output_tokens', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('requests', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_table('ai_usage')
    op.drop_index('ix_ai_tool_calls_tenant_created', table_name='ai_tool_calls')
    op.drop_table('ai_tool_calls')
    op.drop_index('ix_ai_messages_conversation', table_name='ai_messages')
    op.drop_table('ai_messages')
    op.drop_index('ix_ai_conversations_tenant_user', table_name='ai_conversations')
    op.drop_table('ai_conversations')
    op.drop_index('ix_tags_gateway_id', table_name='tags')
    op.drop_column('tags', 'edge')
    op.drop_column('tags', 'gateway_id')
    op.drop_index('ix_gateways_tenant_plant', table_name='gateways')
    op.drop_index('ix_gateways_tenant_id', table_name='gateways')
    op.drop_table('gateways')
