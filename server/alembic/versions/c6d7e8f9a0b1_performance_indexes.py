"""Índices de desempenho (Sprint 7, docs/performance.md §4)

* usage_buckets (scope, entity_id, bucket_start): "desde quando há dados" de uma posição/componente/máquina
  (kpis.md S7-2) e as janelas de ritmo por entidade. A chave primária tem `measure` entre entity_id e bucket_start,
  então min(bucket_start) por entidade não a usava: com o plano genérico das consultas preparadas o Postgres
  percorria ix_usage_buckets_tenant_bucket do tenant inteiro (100 080 linhas descartadas, 27 ms por chamada; 124
  chamadas num ciclo de /api/maintenance-plans/status) — com o índice, 0,05 ms.

Criado com CONCURRENTLY (sem bloquear a ingestão numa base grande em produção).

Revision ID: c6d7e8f9a0b1
Revises: d7e8f9a0b1c2
Create Date: 2026-10-07 19:00:00.000000
"""
from alembic import op

revision = 'c6d7e8f9a0b1'
down_revision = 'd7e8f9a0b1c2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index('ix_usage_buckets_entity_start', 'usage_buckets', ['scope', 'entity_id', 'bucket_start'],
                        postgresql_concurrently=True, if_not_exists=True)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index('ix_usage_buckets_entity_start', table_name='usage_buckets', postgresql_concurrently=True,
                      if_exists=True)
