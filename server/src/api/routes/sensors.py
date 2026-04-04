from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from fastapi import APIRouter, Depends, Query

from ...models import User
from ...auth import get_current_user
from ...services.influx import influx

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
    values = await influx.latest(tenant_id=user.tenant_id, tag_ids=tag_ids)
    return values
