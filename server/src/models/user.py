from uuid import uuid4
from datetime import datetime
from typing import Optional
import enum

from sqlalchemy import BigInteger, String, Boolean, DateTime, ForeignKey, Enum as SAEnum, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class UserRole(str, enum.Enum):
    operator   = "operator"
    manager    = "manager"
    admin      = "admin"
    superadmin = "superadmin"
    # Sprint 4 — manutenção (maintenance_workflows.md §1). Um usuário pode ter vários papéis (roles).
    maintenance_planner   = "maintenance_planner"
    technician            = "technician"
    production_supervisor = "production_supervisor"


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
    # Todos os papéis do usuário (inclui `role`, o papel principal mantido por compatibilidade).
    roles:         Mapped[list[str]]     = mapped_column(ARRAY(String(32)), nullable=False, default=list, server_default=text("'{}'::varchar[]"))
    # Área (linhas) do usuário: notificações e "OS da minha equipe". Vazio = todas as linhas.
    line_ids:      Mapped[list[str]]     = mapped_column(ARRAY(UUID(as_uuid=False)), nullable=False, default=list, server_default=text("'{}'::uuid[]"))
    # Técnico de plantão: recebe o chamado emergencial (push com som) das linhas da sua área.
    on_call:       Mapped[bool]          = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    # Sprint 7 (backend B, docs/security.md): MFA (TOTP secret AES-GCM encrypted), password age, global token cutoff
    # ("sair de todos os dispositivos" / password reset) and LGPD anonymization.
    mfa_secret_enc:      Mapped[Optional[str]]      = mapped_column(String(255), nullable=True)
    mfa_pending_enc:     Mapped[Optional[str]]      = mapped_column(String(255), nullable=True)
    mfa_enabled_at:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_last_step:       Mapped[Optional[int]]      = mapped_column(BigInteger, nullable=True)
    password_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    tokens_valid_after:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    anonymized_at:       Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def role_set(self) -> set[str]:
        """Papéis efetivos: `roles` ∪ {role}."""
        out = {r.value if isinstance(r, UserRole) else str(r) for r in (self.roles or [])}
        if self.role is not None:
            out.add(self.role.value if isinstance(self.role, UserRole) else str(self.role))
        return out

    @property
    def all_roles(self) -> list[str]:
        primary = self.role.value if isinstance(self.role, UserRole) else str(self.role)
        return [primary] + sorted(r for r in self.role_set if r != primary)

    tenant = relationship("Tenant", back_populates="users")
