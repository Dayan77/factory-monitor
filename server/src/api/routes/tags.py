from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_db
from ...models import User, UserRole, Tag, Machine, Position
from ...models.tag import LINKED_ROLES, PART_ROLES, SemanticRole
from ...auth import get_current_user, require_roles
from ..schemas import TagOut, TagCreate, TagUpdate
from ..tenant_refs import ensure_owned
from ...services.validation import is_uuid
from ..patching import apply_patch

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]
DB          = Annotated[AsyncSession, Depends(get_db)]


async def _get_tag_or_404(tag_id: str, tenant_id: str, db: AsyncSession) -> Tag:
    if not is_uuid(tag_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag não encontrada.")
    result = await db.execute(select(Tag).where(Tag.id == tag_id, Tag.tenant_id == tenant_id))
    tag = result.scalar_one_or_none()
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag não encontrada.")
    return tag


async def _resolve_links(db: AsyncSession, tenant_id: str, role: SemanticRole,
                         machine_id: str | None, position_id: str | None) -> tuple[str | None, str | None]:
    """Validates machine/position references (same tenant, position on that machine) and the
    domain.md §3.1 rule: cycle_*, run_state, stroke_time → position or machine; part_counter_* → machine."""
    if machine_id is not None:
        machine_id = await ensure_owned(db, Machine, machine_id, tenant_id, "machine_id")
    if position_id is not None:
        position_id = await ensure_owned(db, Position, position_id, tenant_id, "position_id")
        pos_machine = (await db.execute(select(Position.machine_id).where(Position.id == position_id))).scalar_one()
        if machine_id is None:
            machine_id = pos_machine
        elif machine_id != pos_machine:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="position_id pertence a outra máquina.")
    if role in LINKED_ROLES and machine_id is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Tag {role.value} deve estar ligada a uma posição ou máquina.")
    if role in PART_ROLES and machine_id is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Tag {role.value} deve estar ligada a uma máquina.")
    return machine_id, position_id


@router.get("/", response_model=list[TagOut])
async def list_tags(user: CurrentUser, db: DB, machine_id: str | None = None,
                    position_id: str | None = None, semantic_role: SemanticRole | None = None):
    q = select(Tag).where(Tag.tenant_id == user.tenant_id, Tag.is_active.is_(True))
    if machine_id:
        if not is_uuid(machine_id):
            return []
        q = q.where(Tag.machine_id == machine_id)
    if position_id:
        if not is_uuid(position_id):
            return []
        q = q.where(Tag.position_id == position_id)
    if semantic_role:
        q = q.where(Tag.semantic_role == semantic_role)
    result = await db.execute(q.order_by(Tag.name))
    return result.scalars().all()


@router.post("/", response_model=TagOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(UserRole.admin))])
async def create_tag(body: TagCreate, user: CurrentUser, db: DB):
    body.machine_id, body.position_id = await _resolve_links(
        db, user.tenant_id, body.semantic_role, body.machine_id, body.position_id)
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
    sent = body.model_fields_set
    role = body.semantic_role if body.semantic_role is not None else tag.semantic_role
    machine_id = body.machine_id if "machine_id" in sent else tag.machine_id
    position_id = body.position_id if "position_id" in sent else tag.position_id
    if "machine_id" in sent and "position_id" not in sent and position_id is not None:
        position_id = None if body.machine_id != tag.machine_id else position_id
    body.machine_id, body.position_id = await _resolve_links(db, user.tenant_id, role, machine_id, position_id)
    apply_patch(tag, body, nullable=frozenset({"machine_id", "position_id", "counter_max", "max_rate_per_s",
                                               "description", "unit", "min_value", "max_value"}),
                skip=frozenset({"machine_id", "position_id"}))
    tag.machine_id, tag.position_id = body.machine_id, body.position_id
    await db.commit()
    await db.refresh(tag)
    return tag


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_roles(UserRole.admin))])
async def delete_tag(tag_id: str, user: CurrentUser, db: DB):
    tag = await _get_tag_or_404(tag_id, user.tenant_id, db)
    await db.delete(tag)
    await db.commit()
