from uuid import uuid4
from typing import Optional
from datetime import datetime
import enum

from sqlalchemy import String, Boolean, Float, Integer, ForeignKey, DateTime, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class AlertCondition(str, enum.Enum):
    gt     = "gt"
    gte    = "gte"
    lt     = "lt"
    lte    = "lte"
    eq     = "eq"
    neq    = "neq"
    change = "change"


class AlertSeverity(str, enum.Enum):
    info     = "info"
    warning  = "warning"
    critical = "critical"


class AlertRule(Base, TimestampMixin):
    __tablename__ = "alert_rules"

    id:               Mapped[str]             = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    tenant_id:        Mapped[str]             = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    tag_id:           Mapped[str]             = mapped_column(UUID(as_uuid=False), ForeignKey("tags.id",    ondelete="CASCADE"), nullable=False)
    name:             Mapped[str]             = mapped_column(String(255), nullable=False)
    condition:        Mapped[AlertCondition]  = mapped_column(SAEnum(AlertCondition), nullable=False)
    threshold:        Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    severity:         Mapped[AlertSeverity]   = mapped_column(SAEnum(AlertSeverity), nullable=False, default=AlertSeverity.warning)
    cooldown_minutes: Mapped[int]             = mapped_column(Integer, nullable=False, default=5)
    notify_email:     Mapped[bool]            = mapped_column(Boolean, nullable=False, default=True)
    notify_telegram:  Mapped[bool]            = mapped_column(Boolean, nullable=False, default=False)
    notify_whatsapp:  Mapped[bool]            = mapped_column(Boolean, nullable=False, default=False)
    is_active:        Mapped[bool]            = mapped_column(Boolean, nullable=False, default=True)

    tag    = relationship("Tag", back_populates="rules")
    events = relationship("AlertEvent", back_populates="rule", lazy="noload")


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id:               Mapped[str]             = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    tenant_id:        Mapped[str]             = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    rule_id:          Mapped[str]             = mapped_column(UUID(as_uuid=False), ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False)
    triggered_at:     Mapped[datetime]        = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    value_at_trigger: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notified:         Mapped[bool]            = mapped_column(Boolean, nullable=False, default=False)

    rule = relationship("AlertRule", back_populates="events")
