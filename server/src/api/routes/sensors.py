from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ...models import User
from ...auth import get_current_user
from ...services.influx import influx
from ...services.validation import InvalidIdentifier, ensure_flux_duration, ensure_uuid

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("/history")
async def sensor_history(
    user: CurrentUser,
    tag_id: str,
    start: Optional[datetime] = Query(default=None),
    stop:  Optional[datetime] = Query(default=None),
    window: str = Query(default="1m"),
):
    try:
        tag_id = ensure_uuid(tag_id, "tag_id")
        window = ensure_flux_duration(window, "window")
    except InvalidIdentifier as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    rows = await influx.query_history(
        tenant_id=user.tenant_id,
        tag_id=tag_id,
        start=start,
        stop=stop,
        window=window,
    )
    return {"tag_id": tag_id, "points": rows}


@router.get("/latest")
async def sensor_latest(
    user: CurrentUser,
    tag_ids: list[str] = Query(),
):
    try:
        tag_ids = [ensure_uuid(t, "tag_ids") for t in tag_ids]
    except InvalidIdentifier as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    values = await influx.latest(tenant_id=user.tenant_id, tag_ids=tag_ids)
    return values
