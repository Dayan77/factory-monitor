from uuid import uuid4
from typing import Optional
import enum

from sqlalchemy import String, Boolean, Float, Integer, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
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

    tenant  = relationship("Tenant",  back_populates="tags")
    machine = relationship("Machine", back_populates="tags")
    rules   = relationship("AlertRule", back_populates="tag", lazy="noload")
