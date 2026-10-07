"""Generic audit trail (audit_events): who did what, on which entity. Never secrets or personal data."""
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditEvent


async def record(db: AsyncSession, tenant_id: str, user_id: Optional[str], action: str, entity_type: str,
                 entity_id: Optional[str] = None, data: Optional[dict] = None) -> AuditEvent:
    ev = AuditEvent(tenant_id=tenant_id, user_id=user_id, action=action, entity_type=entity_type,
                    entity_id=entity_id, data=dict(data or {}))
    db.add(ev)
    await db.flush()
    return ev
