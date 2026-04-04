from uuid import uuid4
from datetime import datetime
from typing import Optional
import enum

from sqlalchemy import String, Boolean, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class UserRole(str, enum.Enum):
    operator   = "operator"
    manager    = "manager"
    admin      = "admin"
    superadmin = "superadmin"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id:            Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    tenant_id:     Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    email:         Mapped[str]           = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str]           = mapped_column(String, nullable=False)
    name:          Mapped[str]           = mapped_column(String(255), nullable=False)
    role:          Mapped[UserRole]      = mapped_column(SAEnum(UserRole), nullable=False, default=UserRole.operator)
    is_active:     Mapped[bool]          = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant = relationship("Tenant", back_populates="users")
