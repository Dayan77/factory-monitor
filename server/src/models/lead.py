from uuid import uuid4
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Boolean, DateTime, Text, func, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class Lead(Base):
    """Contato do site público (sem tenant). IP guardado só como hash (anti-abuso, LGPD)."""
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_ip_hash_created", "ip_hash", "created_at"),
    )

    id:           Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    name:         Mapped[str]                = mapped_column(String(255), nullable=False)
    email:        Mapped[str]                = mapped_column(String(320), nullable=False)
    company:      Mapped[Optional[str]]      = mapped_column(String(255), nullable=True)
    phone:        Mapped[Optional[str]]      = mapped_column(String(64), nullable=True)
    message:      Mapped[Optional[str]]      = mapped_column(Text, nullable=True)
    consent:      Mapped[bool]               = mapped_column(Boolean, nullable=False)
    consent_at:   Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    source:       Mapped[Optional[str]]      = mapped_column(String(255), nullable=True)
    ip_hash:      Mapped[str]                = mapped_column(String(64), nullable=False)
    user_agent:   Mapped[Optional[str]]      = mapped_column(String(500), nullable=True)
    notified_at:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notify_error: Mapped[Optional[str]]      = mapped_column(String(500), nullable=True)
    created_at:   Mapped[datetime]           = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
