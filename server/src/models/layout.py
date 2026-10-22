"""Mapa da planta (Sprint 6 — docs/sprint6.md §1.1): planta baixa enviada pelo cliente + marcadores.

Uma planta tem no máximo um layout `published` e um `draft` por vez; editar um publicado cria um `draft`
(versão + 1, cópia dos marcadores e da imagem); publicar arquiva o anterior. Coordenadas dos marcadores são
normalizadas (`x, y ∈ [0, 1]`, fração da largura/altura da imagem, origem no canto superior esquerdo, centro do
marcador). A imagem fica no volume de arquivos do tenant (FILES_DIR), nunca no banco: aqui só o id do arquivo.
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import (Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String,
                        UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin

LAYOUT_STATUSES = ("draft", "published", "archived")
MARKER_KINDS = ("machine", "line_label", "area_label")
MARKER_SIZES = ("s", "m", "l")

_uuid = lambda: str(uuid4())  # noqa: E731


class PlantLayout(Base, TimestampMixin):
    __tablename__ = "plant_layouts"
    __table_args__ = (
        CheckConstraint(f"status IN {LAYOUT_STATUSES}", name="ck_plant_layouts_status"),
        UniqueConstraint("plant_id", "version", name="uq_plant_layouts_plant_version"),
        Index("uq_plant_layouts_one_published", "plant_id", unique=True, postgresql_where=text("status = 'published'")),
        Index("uq_plant_layouts_one_draft", "plant_id", unique=True, postgresql_where=text("status = 'draft'")),
    )

    id:              Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:       Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    plant_id:        Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("plants.id", ondelete="CASCADE"), nullable=False, index=True)
    version:         Mapped[int]                = mapped_column(Integer, nullable=False)
    status:          Mapped[str]                = mapped_column(String(16), nullable=False, default="draft")
    # arquivo no volume do tenant: FILES_DIR/<tenant_id>/layouts/<image_asset_id>.<ext>
    image_asset_id:  Mapped[Optional[str]]      = mapped_column(String(64), nullable=True)
    image_content_type: Mapped[Optional[str]]   = mapped_column(String(64), nullable=True)
    image_bytes:     Mapped[Optional[int]]      = mapped_column(Integer, nullable=True)
    image_width_px:  Mapped[Optional[int]]      = mapped_column(Integer, nullable=True)
    image_height_px: Mapped[Optional[int]]      = mapped_column(Integer, nullable=True)
    scale_m_per_px:  Mapped[Optional[float]]    = mapped_column(Float, nullable=True)
    # a imagem foi trocada por outra de proporção diferente (> 2 %): marcadores mantidos, posições a revisar
    needs_review:    Mapped[bool]               = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_by:      Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_by:    Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_at:    Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class LayoutMarker(Base, TimestampMixin):
    __tablename__ = "layout_markers"
    __table_args__ = (
        CheckConstraint(f"kind IN {MARKER_KINDS}", name="ck_layout_markers_kind"),
        CheckConstraint(f"size IN {MARKER_SIZES}", name="ck_layout_markers_size"),
        CheckConstraint("x >= 0 AND x <= 1 AND y >= 0 AND y <= 1", name="ck_layout_markers_xy"),
        CheckConstraint("rotation_deg >= 0 AND rotation_deg < 360", name="ck_layout_markers_rotation"),
        CheckConstraint("(kind = 'machine') = (machine_id IS NOT NULL)", name="ck_layout_markers_machine"),
        CheckConstraint("(kind = 'line_label') = (line_id IS NOT NULL)", name="ck_layout_markers_line"),
        Index("uq_layout_markers_machine", "layout_id", "machine_id", unique=True,
              postgresql_where=text("machine_id IS NOT NULL")),
    )

    id:           Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:    Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    layout_id:    Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("plant_layouts.id", ondelete="CASCADE"), nullable=False, index=True)
    kind:         Mapped[str]           = mapped_column(String(16), nullable=False)
    machine_id:   Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("machines.id", ondelete="CASCADE"), nullable=True)
    line_id:      Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("lines.id", ondelete="CASCADE"), nullable=True)
    label:        Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    x:            Mapped[float]         = mapped_column(Float, nullable=False)
    y:            Mapped[float]         = mapped_column(Float, nullable=False)
    rotation_deg: Mapped[float]         = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
    size:         Mapped[str]           = mapped_column(String(1), nullable=False, default="m", server_default="m")
