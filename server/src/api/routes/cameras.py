from typing import Annotated, AsyncIterator

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db import get_db
from ...models import Camera, User, UserRole
from ...auth import get_current_user, require_roles
from ..schemas import CameraCreate, CameraOut, CameraUpdate

router = APIRouter()
log = structlog.get_logger()

CurrentUser = Annotated[User, Depends(get_current_user)]
DB          = Annotated[AsyncSession, Depends(get_db)]

_MEDIAMTX_API = settings.mediamtx_api_url
_MEDIAMTX_HLS = settings.mediamtx_hls_url


async def _get_camera_or_404(camera_id: str, tenant_id: str, db: AsyncSession) -> Camera:
    result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.tenant_id == tenant_id)
    )
    cam = result.scalar_one_or_none()
    if not cam:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Câmera não encontrada.")
    return cam


async def _register_stream(camera_id: str, rtsp_url: str) -> None:
    """Register RTSP source in MediaMTX (best-effort, non-fatal)."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{_MEDIAMTX_API}/v3/config/paths/add/{camera_id}",
                json={"source": rtsp_url},
            )
    except Exception as exc:
        log.warning("mediamtx.register_failed", camera_id=camera_id, error=str(exc))


async def _unregister_stream(camera_id: str) -> None:
    """Remove RTSP source from MediaMTX (best-effort, non-fatal)."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.delete(f"{_MEDIAMTX_API}/v3/config/paths/delete/{camera_id}")
    except Exception as exc:
        log.warning("mediamtx.unregister_failed", camera_id=camera_id, error=str(exc))


# ── CRUD ──────────────────────────────────────────────────────────

@router.get("/", response_model=list[CameraOut])
async def list_cameras(user: CurrentUser, db: DB, machine_id: str | None = None):
    q = select(Camera).where(Camera.tenant_id == user.tenant_id)
    if machine_id:
        q = q.where(Camera.machine_id == machine_id)
    result = await db.execute(q.order_by(Camera.name))
    return result.scalars().all()


@router.post("/", response_model=CameraOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(UserRole.admin))])
async def create_camera(body: CameraCreate, user: CurrentUser, db: DB):
    camera = Camera(tenant_id=user.tenant_id, **body.model_dump())
    db.add(camera)
    await db.commit()
    await db.refresh(camera)
    await _register_stream(camera.id, camera.rtsp_url)
    return camera


@router.get("/{camera_id}", response_model=CameraOut)
async def get_camera(camera_id: str, user: CurrentUser, db: DB):
    return await _get_camera_or_404(camera_id, user.tenant_id, db)


@router.patch("/{camera_id}", response_model=CameraOut,
              dependencies=[Depends(require_roles(UserRole.manager))])
async def update_camera(camera_id: str, body: CameraUpdate, user: CurrentUser, db: DB):
    camera = await _get_camera_or_404(camera_id, user.tenant_id, db)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(camera, field, value)
    await db.commit()
    await db.refresh(camera)
    # Re-register if RTSP URL changed
    if body.rtsp_url is not None:
        await _register_stream(camera.id, camera.rtsp_url)
    return camera


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_roles(UserRole.admin))])
async def delete_camera(camera_id: str, user: CurrentUser, db: DB):
    camera = await _get_camera_or_404(camera_id, user.tenant_id, db)
    await db.delete(camera)
    await db.commit()
    await _unregister_stream(camera_id)


# ── HLS Proxy ─────────────────────────────────────────────────────
# Proxies MediaMTX HLS segments through /api so they stay on HTTPS.

@router.get("/{camera_id}/hls/{file_path:path}")
async def hls_proxy(camera_id: str, file_path: str, user: CurrentUser, db: DB):
    # Verify tenant owns this camera
    await _get_camera_or_404(camera_id, user.tenant_id, db)

    upstream = f"{_MEDIAMTX_HLS}/{camera_id}/{file_path}"

    async def stream_response(url: str) -> AsyncIterator[bytes]:
        async with httpx.AsyncClient(timeout=30) as client:
            async with client.stream("GET", url) as r:
                r.raise_for_status()
                async for chunk in r.aiter_bytes(chunk_size=8192):
                    yield chunk

    try:
        async with httpx.AsyncClient(timeout=10) as probe:
            head = await probe.head(upstream)
            content_type = head.headers.get("content-type", "application/octet-stream")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail="Stream não disponível.")
    except Exception:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="MediaMTX inacessível.")

    return StreamingResponse(stream_response(upstream), media_type=content_type)
