from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_db
from ...models import User, UserRole, Line
from ...auth import get_current_user, require_roles
from ...services.validation import is_uuid
from ..schemas import LineOut, LineCreate, LineUpdate

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]
DB          = Annotated[AsyncSession, Depends(get_db)]


async def _get_line_or_404(line_id: str, tenant_id: str, db: AsyncSession) -> Line:
    if not is_uuid(line_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Linha não encontrada.")
    result = await db.execute(select(Line).where(Line.id == line_id, Line.tenant_id == tenant_id))
    line = result.scalar_one_or_none()
    if not line:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Linha não encontrada.")
    return line


@router.get("/", response_model=list[LineOut])
async def list_lines(user: CurrentUser, db: DB):
    result = await db.execute(select(Line).where(Line.tenant_id == user.tenant_id).order_by(Line.name))
    return result.scalars().all()


@router.post("/", response_model=LineOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(UserRole.admin))])
async def create_line(body: LineCreate, user: CurrentUser, db: DB):
    line = Line(tenant_id=user.tenant_id, **body.model_dump())
    db.add(line)
    await db.commit()
    await db.refresh(line)
    return line


@router.get("/{line_id}", response_model=LineOut)
async def get_line(line_id: str, user: CurrentUser, db: DB):
    return await _get_line_or_404(line_id, user.tenant_id, db)


@router.patch("/{line_id}", response_model=LineOut,
              dependencies=[Depends(require_roles(UserRole.admin))])
async def update_line(line_id: str, body: LineUpdate, user: CurrentUser, db: DB):
    line = await _get_line_or_404(line_id, user.tenant_id, db)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(line, field, value)
    await db.commit()
    await db.refresh(line)
    return line


@router.delete("/{line_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_roles(UserRole.admin))])
async def delete_line(line_id: str, user: CurrentUser, db: DB):
    line = await _get_line_or_404(line_id, user.tenant_id, db)
    await db.delete(line)
    await db.commit()
