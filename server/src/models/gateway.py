"""Gateways de campo (Sprint 5 — gestão da frota, configuração remota e cadastro).

Um gateway pertence a um tenant e a uma planta. O dispositivo se autentica com um token próprio
(`Authorization: Gateway <token>`; só o hash fica no banco), obtido uma única vez pelo código de cadastro
(enrollment, 24 h, uso único). As tags da planta atribuídas a ele (`tags.gateway_id`) formam o mapa v1
servido em `GET /api/gateways/{id}/config` (contrato do edge: edge/src/remote_config.py).
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin

GATEWAY_STATUSES = ("pending", "online", "degraded", "offline")


class Gateway(Base, TimestampMixin):
    __tablename__ = "gateways"
    __table_args__ = (Index("ix_gateways_tenant_plant", "tenant_id", "plant_id"),)

    id:            Mapped[str]                = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    tenant_id:     Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    plant_id:      Mapped[str]                = mapped_column(UUID(as_uuid=False), ForeignKey("plants.id", ondelete="CASCADE"), nullable=False)
    name:          Mapped[str]                = mapped_column(String(120), nullable=False)
    description:   Mapped[Optional[str]]      = mapped_column(String(500), nullable=True)
    # sha256 do token do dispositivo (nunca o token); prefixo visível para o admin reconhecer
    token_hash:    Mapped[Optional[str]]      = mapped_column(String(64), nullable=True, unique=True)
    token_prefix:  Mapped[Optional[str]]      = mapped_column(String(16), nullable=True)
    enrolled_at:   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # código de cadastro de uso único (sha256) e validade (24 h)
    enroll_code_hash:  Mapped[Optional[str]]      = mapped_column(String(64), nullable=True, unique=True)
    enroll_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    version:       Mapped[Optional[str]]      = mapped_column(String(64), nullable=True)
    # último estado informado pelo heartbeat (online | degraded); "offline" é derivado de last_seen_at
    status:        Mapped[str]                = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    last_heartbeat: Mapped[Optional[dict]]    = mapped_column(JSONB, nullable=True)
    heartbeat_s:   Mapped[float]              = mapped_column(Float, nullable=False, default=10.0, server_default="10")
    pulse_poll_ms: Mapped[int]                = mapped_column(Integer, nullable=False, default=20, server_default="20")
    # dispositivos (sources do mapa v1: OPC UA / Modbus) — [{id, protocol, endpoint|host, port, ...}]
    devices:       Mapped[list]               = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    # versão da configuração: incrementa sempre que o mapa efetivo muda (hash do conteúdo)
    config_version: Mapped[int]               = mapped_column(Integer, nullable=False, default=0, server_default="0")
    config_hash:   Mapped[Optional[str]]      = mapped_column(String(64), nullable=True)
    config_fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # usuário MQTT do gateway (ACL do broker de produção: factory/<tenant_id>/#) e hash PBKDF2 do mosquitto
    mqtt_username: Mapped[Optional[str]]      = mapped_column(String(64), nullable=True, unique=True)
    mqtt_password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
