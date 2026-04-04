from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_db
from ...models import User, UserRole, Tag
from ...auth import get_current_user, require_roles
from ..schemas import TagOut, TagCreate, TagUpdate

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]
DB          = Annotated[AsyncSession, Depends(get_db)]


async def _get_tag_or_404(tag_id: str, tenant_id: str, db: AsyncSession) -> Tag:
    result = await db.execute(select(Tag).where(Tag.id == tag_id, Tag.tenant_id == tenant_id))
    tag = result.scalar_one_or_none()
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag não encontrada.")
    return tag


@router.get("/", response_model=list[TagOut])
async def list_tags(user: CurrentUser, db: DB, machine_id: str | None = None):
    q = select(Tag).where(Tag.tenant_id == user.tenant_id, Tag.is_active.is_(True))
    if machine_id:
        q = q.where(Tag.machine_id == machine_id)
    result = await db.execute(q.order_by(Tag.name))
    return result.scalars().all()


@router.post("/", response_model=TagOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(UserRole.admin))])
async def create_tag(body: TagCreate, user: CurrentUser, db: DB):
    tag = Tag(tenant_id=user.tenant_id, **body.model_dump())
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag


@router.get("/{tag_id}", response_model=TagOut)
async def get_tag(tag_id: str, user: CurrentUser, db: DB):
    return await _get_tag_or_404(tag_id, user.tenant_id, db)


@router.patch("/{tag_id}", response_model=TagOut,
              dependencies=[Depends(require_roles(UserRole.admin))])
async def update_tag(tag_id: str, body: TagUpdate, user: CurrentUser, db: DB):
    tag = await _get_tag_or_404(tag_id, user.tenant_id, db)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(tag, field, value)
    await db.commit()
    await db.refresh(tag)
    return tag


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_roles(UserRole.admin))])
async def delete_tag(tag_id: str, user: CurrentUser, db: DB):
    tag = await _get_tag_or_404(tag_id, user.tenant_id, db)
    await db.delete(tag)
    await db.commit()
