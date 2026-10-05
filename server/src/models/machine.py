from uuid import uuid4
from datetime import datetime
from typing import Optional
import enum

from sqlalchemy import String, Boolean, DateTime, Float, Integer, ForeignKey, Enum as SAEnum, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class MachineStatus(str, enum.Enum):
    running     = "running"
    stopped     = "stopped"
    maintenance = "maintenance"
    error       = "error"
    unknown     = "unknown"


class Machine(Base, TimestampMixin):
    __tablename__ = "machines"

    id:            Mapped[str]                  = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    tenant_id:     Mapped[str]                  = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id",  ondelete="CASCADE"), nullable=False)
    line_id:       Mapped[str]                  = mapped_column(UUID(as_uuid=False), ForeignKey("lines.id",    ondelete="CASCADE"), nullable=False)
    name:          Mapped[str]                  = mapped_column(String(255), nullable=False)
    model:         Mapped[Optional[str]]         = mapped_column(String(255), nullable=True)
    serial_number: Mapped[Optional[str]]         = mapped_column(String(255), nullable=True)
    status:        Mapped[MachineStatus]         = mapped_column(SAEnum(MachineStatus), nullable=False, default=MachineStatus.unknown)
    # Last time a reading arrived for any of this machine's tags (server receive time).
    last_seen_at:  Mapped[Optional[datetime]]    = mapped_column(DateTime(timezone=True), nullable=True)
    # Tempo de ciclo ideal padrão da máquina (s). Fase 2 define por (máquina, produto); aqui é
    # usado como max_rate padrão do filtro de plausibilidade de contadores (domain.md §3.3 regra 4).
    ideal_cycle_time_s: Mapped[Optional[float]]  = mapped_column(Float, nullable=True)
    # Parada mais curta que isto é micro-parada (domain.md §4.3 regra 3, kpis.md §1.8; 30–300 s).
    micro_stop_threshold_s: Mapped[int]          = mapped_column(Integer, nullable=False, default=120, server_default=text("120"))
    # Código do fault_state → motivo sugerido (regra 6), ex. {"101": "EQP-01"}. Sem mapa → EQP-01.
    fault_code_map: Mapped[dict]                 = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))

    tenant  = relationship("Tenant",  back_populates="machines")
    line    = relationship("Line",    back_populates="machines", foreign_keys=[line_id])
    tags    = relationship("Tag",     back_populates="machine",  lazy="noload")
    cameras = relationship("Camera",  back_populates="machine",  lazy="noload")
