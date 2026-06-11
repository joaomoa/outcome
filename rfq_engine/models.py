import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    balance: Mapped["Balance"] = relationship(back_populates="participant", uselist=False)


class Balance(Base):
    __tablename__ = "balances"
    __table_args__ = (
        CheckConstraint("available >= 0", name="ck_balance_available_nonneg"),
        CheckConstraint("reserved >= 0", name="ck_balance_reserved_nonneg"),
        CheckConstraint("locked >= 0", name="ck_balance_locked_nonneg"),
    )

    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("participants.id"), primary_key=True
    )
    available: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"))
    reserved: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"))
    locked: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"))

    participant: Mapped["Participant"] = relationship(back_populates="balance")


class Request(Base):
    __tablename__ = "requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requester_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("participants.id"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    response_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accept_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    legs: Mapped[list["Leg"]] = relationship(back_populates="request", cascade="all, delete-orphan")


class Leg(Base):
    __tablename__ = "legs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("requests.id"))
    contract_description: Mapped[str] = mapped_column(Text, nullable=False)
    notional: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    leg_index: Mapped[int] = mapped_column(nullable=False)

    request: Mapped["Request"] = relationship(back_populates="legs")
    quotes: Mapped[list["Quote"]] = relationship(back_populates="leg", cascade="all, delete-orphan")
    escrow: Mapped["Escrow | None"] = relationship(back_populates="leg", uselist=False)
    resolution: Mapped["Resolution | None"] = relationship(back_populates="leg", uselist=False)


class Quote(Base):
    __tablename__ = "quotes"
    __table_args__ = (
        Index(
            "uq_one_selected_per_leg",
            "leg_id",
            unique=True,
            postgresql_where="status = 'selected'",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    leg_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("legs.id"))
    mm_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("participants.id"))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 8), nullable=False)
    size: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    reserved_amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    leg: Mapped["Leg"] = relationship(back_populates="quotes")


class Escrow(Base):
    __tablename__ = "escrows"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    leg_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("legs.id"), unique=True)
    requester_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("participants.id"))
    mm_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("participants.id"))
    requester_locked: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    mm_locked: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    leg: Mapped["Leg"] = relationship(back_populates="escrow")


class Resolution(Base):
    __tablename__ = "resolutions"
    __table_args__ = (UniqueConstraint("leg_id", name="uq_resolution_per_leg"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    leg_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("legs.id"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    outcome: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    leg: Mapped["Leg"] = relationship(back_populates="resolution")
