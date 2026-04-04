from uuid import uuid4
from typing import Optional
from sqlalchemy import String, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class Line(Base, TimestampMixin):
    __tablename__ = "lines"

    id:          Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    tenant_id:   Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name:        Mapped[str]           = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    location:    Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active:   Mapped[bool]          = mapped_column(Boolean, nullable=False, default=True)

    tenant   = relationship("Tenant",  back_populates="lines")
    machines = relationship("Machine", back_populates="line", lazy="noload")
