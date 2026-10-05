"""Notificações (maintenance_workflows.md §5), assinaturas Web Push e integração (tokens de API,
webhooks de saída)."""
from uuid import uuid4
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Date, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin

_uuid = lambda: str(uuid4())  # noqa: E731


class Notification(Base):
    """Uma notificação para um usuário. Repetidas do mesmo `group_key` ainda não lidas são
    agrupadas (count + items) em vez de criar outra linha."""
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_unread", "user_id", "read_at", "created_at"),
        Index("ix_notifications_tenant_created", "tenant_id", "created_at"),
    )

    id:         Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:  Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id:    Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind:       Mapped[str]                = mapped_column(String(48), nullable=False)
    title:      Mapped[str]                = mapped_column(String(255), nullable=False)
    body:       Mapped[Optional[str]]      = mapped_column(Text, nullable=True)
    link:       Mapped[Optional[str]]      = mapped_column(String(500), nullable=True)
    priority:   Mapped[str]                = mapped_column(String(16), nullable=False, default="normal")  # low|normal|high|emergency
    data:       Mapped[dict]               = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    group_key:  Mapped[Optional[str]]      = mapped_column(String(255), nullable=True)
    count:      Mapped[int]                = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    line_id:    Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), nullable=True)
    read_at:    Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # canais: entregue por e-mail / push (imediato) e incluída no resumo diário
    emailed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    pushed_at:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    digest:     Mapped[bool]               = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    digested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class NotificationPreference(Base, TimestampMixin):
    """Canais por tipo de notificação: {kind: {"in_app": bool, "email": bool, "web_push": bool}}.
    Chamado emergencial do plantão não pode ser silenciado."""
    __tablename__ = "notification_preferences"

    user_id:        Mapped[str]            = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    tenant_id:      Mapped[str]            = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    channels:       Mapped[dict]           = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    digest_enabled: Mapped[bool]           = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    last_digest_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)


class PushSubscription(Base, TimestampMixin):
    __tablename__ = "push_subscriptions"

    id:         Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:  Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id:    Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    endpoint:   Mapped[str]                = mapped_column(Text, nullable=False, unique=True)
    p256dh:     Mapped[str]                = mapped_column(String(255), nullable=False)
    auth:       Mapped[str]                = mapped_column(String(255), nullable=False)
    user_agent: Mapped[Optional[str]]      = mapped_column(String(255), nullable=True)
    last_error: Mapped[Optional[str]]      = mapped_column(String(500), nullable=True)


class ApiToken(Base, TimestampMixin):
    """Token de integração (CMMS/ERP) por tenant. Só o hash SHA-256 é guardado."""
    __tablename__ = "api_tokens"

    id:           Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:    Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name:         Mapped[str]                = mapped_column(String(255), nullable=False)
    prefix:       Mapped[str]                = mapped_column(String(16), nullable=False)
    token_hash:   Mapped[str]                = mapped_column(String(64), nullable=False, unique=True)
    scopes:       Mapped[list[str]]          = mapped_column(ARRAY(String(16)), nullable=False, default=list)
    created_by:   Mapped[Optional[str]]      = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at:   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at:   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Webhook(Base, TimestampMixin):
    """Webhook de saída: POST JSON assinado (HMAC-SHA256 com `secret`) a cada evento assinado."""
    __tablename__ = "webhooks"

    id:          Mapped[str]           = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:   Mapped[str]           = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    url:         Mapped[str]           = mapped_column(Text, nullable=False)
    secret:      Mapped[str]           = mapped_column(String(255), nullable=False)
    events:      Mapped[list[str]]     = mapped_column(ARRAY(String(64)), nullable=False, default=list)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active:   Mapped[bool]          = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_by:  Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_webhook_deliveries_pending", "status", "next_attempt_at"),)

    id:               Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id:        Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    webhook_id:       Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True)
    event:            Mapped[str]                = mapped_column(String(64), nullable=False)
    payload:          Mapped[dict]               = mapped_column(JSONB, nullable=False)
    status:           Mapped[str]                = mapped_column(String(16), nullable=False, default="pending")  # pending|delivered|failed
    attempts:         Mapped[int]                = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status_code: Mapped[Optional[int]]      = mapped_column(Integer, nullable=True)
    last_error:       Mapped[Optional[str]]      = mapped_column(String(500), nullable=True)
    delivered_at:     Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at:       Mapped[datetime]           = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
