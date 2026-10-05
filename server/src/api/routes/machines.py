from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_db
from ...models import User, UserRole, Machine, Line
from ...auth import get_current_user, require_roles
from ..schemas import MachineOut, MachineCreate, MachineUpdate
from ..tenant_refs import ensure_owned
from ...services.validation import is_uuid

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]
DB          = Annotated[AsyncSession, Depends(get_db)]


async def _get_machine_or_404(machine_id: str, tenant_id: str, db: AsyncSession) -> Machine:
    if not is_uuid(machine_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Máquina não encontrada.")
    result = await db.execute(
        select(Machine).where(Machine.id == machine_id, Machine.tenant_id == tenant_id)
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Máquina não encontrada.")
    return m


@router.get("/", response_model=list[MachineOut])
async def list_machines(user: CurrentUser, db: DB, line_id: str | None = None):
    q = select(Machine).where(Machine.tenant_id == user.tenant_id)
    if line_id:
        q = q.where(Machine.line_id == line_id)
    result = await db.execute(q.order_by(Machine.name))
    return result.scalars().all()


@router.post("/", response_model=MachineOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(UserRole.admin))])
async def create_machine(body: MachineCreate, user: CurrentUser, db: DB):
    body.line_id = await ensure_owned(db, Line, body.line_id, user.tenant_id, "line_id")
    machine = Machine(tenant_id=user.tenant_id, **body.model_dump())
    db.add(machine)
    await db.commit()
    await db.refresh(machine)
    return machine


@router.get("/{machine_id}", response_model=MachineOut)
async def get_machine(machine_id: str, user: CurrentUser, db: DB):
    return await _get_machine_or_404(machine_id, user.tenant_id, db)


@router.patch("/{machine_id}", response_model=MachineOut,
              dependencies=[Depends(require_roles(UserRole.manager))])
async def update_machine(machine_id: str, body: MachineUpdate, user: CurrentUser, db: DB):
    machine = await _get_machine_or_404(machine_id, user.tenant_id, db)
    if body.line_id is not None:
        body.line_id = await ensure_owned(db, Line, body.line_id, user.tenant_id, "line_id")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(machine, field, value)
    await db.commit()
    await db.refresh(machine)
    return machine


@router.delete("/{machine_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_roles(UserRole.admin))])
async def delete_machine(machine_id: str, user: CurrentUser, db: DB):
    machine = await _get_machine_or_404(machine_id, user.tenant_id, db)
    await db.delete(machine)
    await db.commit()
