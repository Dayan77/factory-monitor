from uuid import uuid4
from typing import Optional
import enum

from sqlalchemy import String, Boolean, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class MachineStatus(str, enum.Enum):
    running     = "running"
    stopped     = "stopped"
    maintenance = "maintenance"
    error       = "error"
    unknown     = "unknown"


class Machine(Base, TimestampMixin):
    __tablename__ = "machines"

    id:            Mapped[str]                  = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    tenant_id:     Mapped[str]                  = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id",  ondelete="CASCADE"), nullable=False)
    line_id:       Mapped[str]                  = mapped_column(UUID(as_uuid=False), ForeignKey("lines.id",    ondelete="CASCADE"), nullable=False)
    name:          Mapped[str]                  = mapped_column(String(255), nullable=False)
    model:         Mapped[Optional[str]]         = mapped_column(String(255), nullable=True)
    serial_number: Mapped[Optional[str]]         = mapped_column(String(255), nullable=True)
    status:        Mapped[MachineStatus]         = mapped_column(SAEnum(MachineStatus), nullable=False, default=MachineStatus.unknown)

    tenant = relationship("Tenant",  back_populates="machines")
    line   = relationship("Line",    back_populates="machines")
    tags   = relationship("Tag",     back_populates="machine",  lazy="noload")
