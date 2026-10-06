"""Ingestão MQTT sem perda (Sprint 5b): dead letters, marcas d'água por tag e cursor por bloco de ciclos

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-10-06 16:00:00.000000

* ingest_dead_letters: leitura válida que falhou depois das repetições (nada é descartado em silêncio);
* ingest_watermarks: por (tenant, tag) o maior ts gravado e o intervalo de leituras atrasadas ainda não
  consumido pelo motor preditivo (janelas já extraídas que elas tocam são recalculadas);
* predictive_dirty: intervalos atrasados por monitor, a reprocessar;
* condition_features.carry: ciclos que a leitura que fechou o bloco ultrapassou (retomada a partir de qualquer
  bloco).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'f3a4b5c6d7e8'
down_revision = 'e2f3a4b5c6d7'
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=False)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'ingest_dead_letters',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, nullable=True),
        sa.Column('tag_id', UUID, nullable=True),
        sa.Column('topic', sa.String(512), nullable=False),
        sa.Column('payload', sa.LargeBinary(), nullable=False),
        sa.Column('received_at', TS, nullable=False),
        sa.Column('stage', sa.String(16), nullable=False),
        sa.Column('error', sa.Text(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False),
        sa.Column('replayed_at', TS, nullable=True),
    )
    op.create_index('ix_ingest_dead_letters_tenant_created', 'ingest_dead_letters', ['tenant_id', 'created_at'])
    op.create_table(
        'ingest_watermarks',
        sa.Column('tenant_id', UUID, primary_key=True),
        sa.Column('tag_id', UUID, primary_key=True),
        sa.Column('machine_id', UUID, nullable=True),
        sa.Column('max_ts', TS, nullable=False),
        sa.Column('late_min_ts', TS, nullable=True),
        sa.Column('late_max_ts', TS, nullable=True),
        sa.Column('late_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', TS, server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_ingest_watermarks_late', 'ingest_watermarks', ['tenant_id', 'machine_id'],
                    postgresql_where=sa.text('late_min_ts IS NOT NULL'))
    op.add_column('condition_features', sa.Column('carry', sa.Float(precision=53), nullable=True))
    op.create_table(
        'predictive_dirty',
        sa.Column('id', UUID, primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('monitor_id', UUID, sa.ForeignKey('predictive_monitors.id', ondelete='CASCADE'), nullable=False),
        sa.Column('lo', TS, nullable=False),
        sa.Column('hi', TS, nullable=False),
        sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_predictive_dirty_monitor_id', 'predictive_dirty', ['monitor_id'])


def downgrade() -> None:
    op.drop_index('ix_predictive_dirty_monitor_id', table_name='predictive_dirty')
    op.drop_table('predictive_dirty')
    op.drop_column('condition_features', 'carry')
    op.drop_index('ix_ingest_watermarks_late', table_name='ingest_watermarks')
    op.drop_table('ingest_watermarks')
    op.drop_index('ix_ingest_dead_letters_tenant_created', table_name='ingest_dead_letters')
    op.drop_table('ingest_dead_letters')
