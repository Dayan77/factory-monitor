"""Trilha de auditoria das OS identifica ações feitas por token de API

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-10-05 18:00:00.000000

* work_order_history.api_token_id (FK api_tokens, SET NULL): a transição foi feita por uma integração
  (ERP/CMMS) com esse token — `by` continua sendo o admin que criou o token, mas a UI/API mostram "API: <nome>".
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'c0d1e2f3a4b5'
down_revision = 'b9c0d1e2f3a4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('work_order_history', sa.Column(
        'api_token_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('api_tokens.id', ondelete='SET NULL'),
        nullable=True))


def downgrade() -> None:
    op.drop_column('work_order_history', 'api_token_id')
