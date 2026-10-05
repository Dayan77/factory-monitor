"""Posição funcional e componente físico (domain.md §1, §2)."""
from uuid import uuid4
from datetime import datetime
from typing import Optional
import enum

from sqlalchemy import String, Integer, ForeignKey, DateTime, Enum as SAEnum, Index, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class ComponentType(str, enum.Enum):
    pneumatic_cylinder = "pneumatic_cylinder"
    electric_motor     = "electric_motor"
    servo              = "servo"
    robot_axis         = "robot_axis"
    valve              = "valve"
    gripper            = "gripper"
    bearing            = "bearing"
    belt               = "belt"
    pump               = "pump"
    spindle            = "spindle"
    other              = "other"


class RemovalReason(str, enum.Enum):
    failure                = "failure"
    preventive_replacement = "preventive_replacement"
    upgrade                = "upgrade"
    other                  = "other"


class Position(Base, TimestampMixin):
    """Localização funcional permanente na máquina (ISO 14224 maintainable item location)."""
    __tablename__ = "positions"

    id:              Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    tenant_id:       Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    machine_id:      Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True)
    name:            Mapped[str]           = mapped_column(String(255), nullable=False)
    component_type:  Mapped[ComponentType] = mapped_column(SAEnum(ComponentType), nullable=False)
    cycles_per_part: Mapped[int]           = mapped_column(Integer, nullable=False, default=1)
    counter_tag_id:  Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("tags.id", ondelete="SET NULL", use_alter=True), nullable=True)


class Component(Base, TimestampMixin):
    """Item físico. Removido nunca é apagado; no máximo um instalado por posição."""
    __tablename__ = "components"
    __table_args__ = (
        Index("uq_components_one_installed_per_position", "position_id", unique=True,
              postgresql_where=text("position_id IS NOT NULL AND removed_at IS NULL")),
    )

    id:               Mapped[str]                   = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    tenant_id:        Mapped[str]                   = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    position_id:      Mapped[Optional[str]]         = mapped_column(UUID(as_uuid=False), ForeignKey("positions.id", ondelete="SET NULL"), nullable=True, index=True)
    # Posição em que o componente foi (ou está) instalado — mantida após a remoção, para a
    # janela de atribuição de uso [installed_at, removed_at) e o histórico da posição.
    last_position_id: Mapped[Optional[str]]         = mapped_column(UUID(as_uuid=False), ForeignKey("positions.id", ondelete="SET NULL"), nullable=True, index=True)
    component_type:   Mapped[ComponentType]         = mapped_column(SAEnum(ComponentType), nullable=False)
    manufacturer:     Mapped[Optional[str]]         = mapped_column(String(255), nullable=True)
    model:            Mapped[Optional[str]]         = mapped_column(String(255), nullable=True)
    serial_number:    Mapped[Optional[str]]         = mapped_column(String(255), nullable=True)
    installed_at:     Mapped[Optional[datetime]]    = mapped_column(DateTime(timezone=True), nullable=True)
    removed_at:       Mapped[Optional[datetime]]    = mapped_column(DateTime(timezone=True), nullable=True)
    removal_reason:   Mapped[Optional[RemovalReason]] = mapped_column(SAEnum(RemovalReason), nullable=True)
    # {"cycles": int?, "hours": float?, "starts": int?}
    nominal_life:     Mapped[dict]                  = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
