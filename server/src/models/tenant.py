from uuid import uuid4
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    users    = relationship("User",    back_populates="tenant", lazy="noload")
    lines    = relationship("Line",    back_populates="tenant", lazy="noload")
    machines = relationship("Machine", back_populates="tenant", lazy="noload")
    tags     = relationship("Tag",     back_populates="tenant", lazy="noload")
