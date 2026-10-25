from uuid import uuid4
from typing import Optional

from sqlalchemy import String, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin

DEFAULT_TIMEZONE = "America/Sao_Paulo"


class Plant(Base, TimestampMixin):
    """Planta (domain.md §1). O fuso (IANA) define o dia produtivo e os turnos."""
    __tablename__ = "plants"

    id:        Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name:      Mapped[str]           = mapped_column(String(255), nullable=False)
    timezone:  Mapped[str]           = mapped_column(String(64), nullable=False, default=DEFAULT_TIMEZONE)
    address:   Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    lines = relationship("Line", back_populates="plant", lazy="noload")
