"""Fase 2 (correções do sprint 3): dados atrasados no detector de paradas e versões de turno

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-10-07 12:00:00.000000

* downtime_detector_states.late_from — menor timestamp de leitura que chegou ao InfluxDB depois que
  o checkpoint do detector já tinha passado por ele (buffer do gateway, replay): a próxima passada
  volta (rewind) e re-detecta a partir de um snapshot anterior.
* downtime_detector_snapshots — checkpoints antigos do detector (estado dos sinais + micro-paradas
  já definitivas da hora do checkpoint) para o rewind ser exato.
* shifts.valid_from / valid_to — vigência (dias produtivos, inclusivos) de uma versão do turno:
  editar horário/dias/pausas ou desativar um turno com histórico fecha a versão antiga em vez de
  reescrever o passado.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'a8b9c0d1e2f3'
down_revision = 'f7a8b9c0d1e2'
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=False)


def upgrade() -> None:
    op.add_column('downtime_detector_states', sa.Column('late_from', sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        'downtime_detector_snapshots',
        sa.Column('machine_id', UUID, sa.ForeignKey('machines.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('checkpoint', sa.DateTime(timezone=True), primary_key=True),
        sa.Column('tenant_id', UUID, sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('state', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('micro_from', sa.DateTime(timezone=True), nullable=True),
        sa.Column('micro', postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_downtime_detector_snapshots_tenant_id', 'downtime_detector_snapshots', ['tenant_id'])
    op.add_column('shifts', sa.Column('valid_from', sa.Date(), nullable=True))
    op.add_column('shifts', sa.Column('valid_to', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('shifts', 'valid_to')
    op.drop_column('shifts', 'valid_from')
    op.drop_index('ix_downtime_detector_snapshots_tenant_id', table_name='downtime_detector_snapshots')
    op.drop_table('downtime_detector_snapshots')
    op.drop_column('downtime_detector_states', 'late_from')
