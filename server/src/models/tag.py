from uuid import uuid4
from typing import Optional
import enum

from sqlalchemy import String, Boolean, Float, Integer, BigInteger, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin


class ProtocolType(str, enum.Enum):
    modbus_tcp = "modbus_tcp"
    opc_ua     = "opc_ua"
    mqtt       = "mqtt"
    http       = "http"


class DataType(str, enum.Enum):
    float  = "float"
    int    = "int"
    bool   = "bool"
    string = "string"


class SemanticRole(str, enum.Enum):
    """Papel semântico da tag (domain.md §3.1). `analog` é o padrão."""
    analog             = "analog"
    cycle_counter      = "cycle_counter"
    cycle_pulse        = "cycle_pulse"
    run_state          = "run_state"
    part_counter_good  = "part_counter_good"
    part_counter_scrap = "part_counter_scrap"
    part_counter_total = "part_counter_total"
    fault_state        = "fault_state"
    stroke_time        = "stroke_time"
    speed              = "speed"
    current            = "current"


COUNTER_ROLES = frozenset({
    SemanticRole.cycle_counter, SemanticRole.cycle_pulse,
    SemanticRole.part_counter_good, SemanticRole.part_counter_scrap, SemanticRole.part_counter_total,
})
CYCLE_ROLES = frozenset({SemanticRole.cycle_counter, SemanticRole.cycle_pulse})
PART_ROLES = frozenset({SemanticRole.part_counter_good, SemanticRole.part_counter_scrap,
                        SemanticRole.part_counter_total})
# Tags que DEVEM estar ligadas a uma posição ou máquina (§3.1)
LINKED_ROLES = frozenset(CYCLE_ROLES | {SemanticRole.run_state, SemanticRole.stroke_time})


class Tag(Base, TimestampMixin):
    __tablename__ = "tags"

    id:               Mapped[str]            = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    tenant_id:        Mapped[str]            = mapped_column(UUID(as_uuid=False), ForeignKey("tenants.id",   ondelete="CASCADE"), nullable=False)
    machine_id:       Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), ForeignKey("machines.id",  ondelete="SET NULL"), nullable=True)
    name:             Mapped[str]            = mapped_column(String(255), nullable=False)
    description:      Mapped[Optional[str]]  = mapped_column(String, nullable=True)
    protocol:         Mapped[ProtocolType]   = mapped_column(SAEnum(ProtocolType), nullable=False)
    address:          Mapped[str]            = mapped_column(String(512), nullable=False)
    data_type:        Mapped[DataType]       = mapped_column(SAEnum(DataType), nullable=False, default=DataType.float)
    unit:             Mapped[Optional[str]]  = mapped_column(String(50), nullable=True)
    scale_factor:     Mapped[float]          = mapped_column(Float, nullable=False, default=1.0)
    offset:           Mapped[float]          = mapped_column(Float, nullable=False, default=0.0)
    min_value:        Mapped[Optional[float]]= mapped_column(Float, nullable=True)
    max_value:        Mapped[Optional[float]]= mapped_column(Float, nullable=True)
    poll_interval_ms: Mapped[int]            = mapped_column(Integer, nullable=False, default=1000)
    is_active:        Mapped[bool]           = mapped_column(Boolean, nullable=False, default=True)
    semantic_role:    Mapped[SemanticRole]   = mapped_column(SAEnum(SemanticRole), nullable=False, default=SemanticRole.analog, server_default="analog")
    position_id:      Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), ForeignKey("positions.id", ondelete="SET NULL"), nullable=True, index=True)
    # Valor máximo do contador no CLP (65535 = 16 bits, 4294967295 = 32 bits) — rollover §3.3
    counter_max:      Mapped[Optional[int]]  = mapped_column(BigInteger, nullable=True)
    # Contagens/s máximas plausíveis (filtro §3.3 regra 4). Nulo → 2 / ideal_cycle_time da máquina.
    max_rate_per_s:   Mapped[Optional[float]]= mapped_column(Float, nullable=True)
    # Sprint 5 — gateway de campo que lê esta tag (mapa remoto) e os campos de borda do mapa v1
    # (source, key, unit_id, register_type, data_type_plc, word_order, signal_role): edge/src/plant_map.py
    gateway_id:       Mapped[Optional[str]]  = mapped_column(UUID(as_uuid=False), ForeignKey("gateways.id", ondelete="SET NULL"), nullable=True, index=True)
    edge:             Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    tenant  = relationship("Tenant",  back_populates="tags")
    machine = relationship("Machine", back_populates="tags")
    rules   = relationship("AlertRule", back_populates="tag", lazy="noload")
