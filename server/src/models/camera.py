from uuid import uuid4
from typing import Optional

from sqlalchemy import String, Boolean, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class Camera(Base, TimestampMixin):
    __tablename__ = "cameras"

    id:          Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    tenant_id:   Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    machine_id:  Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("machines.id", ondelete="SET NULL"), nullable=True)
    name:        Mapped[str]           = mapped_column(String(255), nullable=False)
    rtsp_url:    Mapped[str]           = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_active:   Mapped[bool]          = mapped_column(Boolean, nullable=False, default=True)

    tenant  = relationship("Tenant",  back_populates="cameras")
    machine = relationship("Machine", back_populates="cameras")
