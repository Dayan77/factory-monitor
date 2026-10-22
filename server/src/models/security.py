"""Sprint 7 (backend B) — authentication hardening and privacy (docs/security.md, docs/privacy.md).

* auth_sessions: one row per sign-in (= refresh-token family). Access tokens carry `sid`; revoking the session
  (logout, "sair de todos", reuse of a rotated refresh token, password change) kills every token of the family.
* refresh_tokens: each refresh token issued (by jti). A refresh token is single-use: presenting one that was already
  rotated (outside the short grace window) revokes the whole session (token theft).
* auth_throttle: failure counters with temporary lock and exponential backoff (per account, per IP, per MFA user).
* rate_counters: fixed-window counters (login/reset/public endpoint rate limits), shared by all workers.
* password_history: last N password hashes (no reuse).
* password_reset_tokens: single-use reset tokens (the e-mailed link carries a signed JWT naming this row).
* user_invitations: invitation-only signup into a tenant.
* mfa_recovery_codes: hashed one-time recovery codes.
* tenant_security_settings: per-tenant policy (require_mfa_admins).
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

_uuid = lambda: str(uuid4())  # noqa: E731


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_user", "user_id", "revoked_at"),)

    id:             Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:      Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id:        Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at:     Mapped[datetime]           = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_used_at:   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at:     Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at:     Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[Optional[str]]      = mapped_column(String(32), nullable=True)
    ip_prefix:      Mapped[Optional[str]]      = mapped_column(String(64), nullable=True)    # 203.0.113.x (LGPD)
    user_agent:     Mapped[Optional[str]]      = mapped_column(String(300), nullable=True)
    mfa:            Mapped[bool]               = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_session", "session_id"),)

    jti:         Mapped[str]                = mapped_column(String(64), primary_key=True)
    session_id:  Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("auth_sessions.id", ondelete="CASCADE"), nullable=False)
    issued_at:   Mapped[datetime]           = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at:  Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    used_at:     Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by: Mapped[Optional[str]]      = mapped_column(String(64), nullable=True)


class AuthThrottle(Base):
    __tablename__ = "auth_throttle"

    key:          Mapped[str]                = mapped_column(String(128), primary_key=True)
    failures:     Mapped[int]                = mapped_column(Integer, nullable=False, default=0)
    window_start: Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    lock_level:   Mapped[int]                = mapped_column(Integer, nullable=False, default=0)
    updated_at:   Mapped[datetime]           = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class RateCounter(Base):
    __tablename__ = "rate_counters"

    key:          Mapped[str]      = mapped_column(String(128), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count:        Mapped[int]      = mapped_column(Integer, nullable=False, default=0)


class PasswordHistory(Base):
    __tablename__ = "password_history"
    __table_args__ = (Index("ix_password_history_user", "user_id", "created_at"),)

    id:            Mapped[str]      = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id:       Mapped[str]      = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    password_hash: Mapped[str]      = mapped_column(String, nullable=False)
    created_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (Index("ix_password_reset_tokens_user", "user_id"),)

    id:         Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id:    Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime]           = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    used_at:    Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class UserInvitation(Base):
    __tablename__ = "user_invitations"
    __table_args__ = (Index("ix_user_invitations_tenant", "tenant_id", "created_at"),)

    id:          Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:   Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    email:       Mapped[str]                = mapped_column(String(255), nullable=False)
    name:        Mapped[Optional[str]]      = mapped_column(String(255), nullable=True)
    role:        Mapped[str]                = mapped_column(String(32), nullable=False)
    roles:       Mapped[list[str]]          = mapped_column(ARRAY(String(32)), nullable=False, default=list, server_default=text("'{}'::varchar[]"))
    line_ids:    Mapped[list[str]]          = mapped_column(ARRAY(UUID(as_uuid=False)), nullable=False, default=list, server_default=text("'{}'::uuid[]"))
    token_hash:  Mapped[str]                = mapped_column(String(64), nullable=False, unique=True)
    invited_by:  Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    user_id:     Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at:  Mapped[datetime]           = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at:  Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class MfaRecoveryCode(Base):
    __tablename__ = "mfa_recovery_codes"
    __table_args__ = (Index("ix_mfa_recovery_codes_user", "user_id"),)

    id:         Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id:    Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    code_hash:  Mapped[str]                = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime]           = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    used_at:    Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TenantSecuritySettings(Base):
    __tablename__ = "tenant_security_settings"

    tenant_id:          Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    require_mfa_admins: Mapped[bool]          = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    updated_by:         Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at:         Mapped[datetime]      = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
