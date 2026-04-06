from uuid import uuid4
from datetime import datetime
from typing import Optional
import enum

from sqlalchemy import String, Boolean, DateTime, Integer, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class TenantPlan(str, enum.Enum):
    trial        = "trial"
    starter      = "starter"
    professional = "professional"
    enterprise   = "enterprise"


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id:               Mapped[str]            = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    name:             Mapped[str]            = mapped_column(String(255), nullable=False)
    slug:             Mapped[str]            = mapped_column(String(100), nullable=False, unique=True)
    is_active:        Mapped[bool]           = mapped_column(Boolean, nullable=False, default=True)
    plan:             Mapped[TenantPlan]     = mapped_column(SAEnum(TenantPlan), nullable=False, default=TenantPlan.trial)
    trial_ends_at:    Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_reason: Mapped[Optional[str]]  = mapped_column(String(500), nullable=True)
    max_users:        Mapped[int]            = mapped_column(Integer, nullable=False, default=5)
    max_machines:     Mapped[int]            = mapped_column(Integer, nullable=False, default=10)

    users    = relationship("User",    back_populates="tenant", lazy="noload")
    lines    = relationship("Line",    back_populates="tenant", lazy="noload")
    machines = relationship("Machine", back_populates="tenant", lazy="noload")
    tags     = relationship("Tag",     back_populates="tenant", lazy="noload")
    cameras  = relationship("Camera",  back_populates="tenant", lazy="noload")
