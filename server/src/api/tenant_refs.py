"""
Cross-tenant reference guard.

Request bodies carry foreign keys (line_id, machine_id, tag_id). Before
persisting them we must prove the referenced row belongs to the caller's
tenant — otherwise tenant B could attach its records to tenant A's assets.
"""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..services.validation import InvalidIdentifier, ensure_uuid


async def ensure_owned(db: AsyncSession, model, ref_id: str, tenant_id: str, label: str) -> str:
    """Returns the canonical id if `model` row `ref_id` belongs to `tenant_id`.

    422 for a malformed id, 404 when it does not exist *for this tenant*
    (the same answer whether it exists in another tenant or not at all).
    """
    try:
        ref_id = ensure_uuid(ref_id, label)
    except InvalidIdentifier as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    result = await db.execute(
        select(model.id).where(model.id == ref_id, model.tenant_id == tenant_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} não encontrado(a).")
    return ref_id
