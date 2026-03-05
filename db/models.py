from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class BookingStatus(enum.Enum):
    confirmed = "confirmed"
    cancelled_client = "cancelled_client"
    cancelled_admin = "cancelled_admin"
    completed = "completed"


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, unique=True, nullable=True, index=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    bookings: Mapped[List["Booking"]] = relationship("Booking", back_populates="client")
    reviews: Mapped[List["Review"]] = relationship("Review", back_populates="client")


class Master(Base):
    __tablename__ = "masters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    photo_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    services: Mapped[List["MasterService"]] = relationship("MasterService", back_populates="master")
    slots: Mapped[List["Slot"]] = relationship("Slot", back_populates="master")
    bookings: Mapped[List["Booking"]] = relationship("Booking", back_populates="master")


class Service(Base):
    __tablename__ = "services"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    masters: Mapped[List["MasterService"]] = relationship("MasterService", back_populates="service")
    bookings: Mapped[List["Booking"]] = relationship("Booking", back_populates="service")


class MasterService(Base):
    __tablename__ = "master_services"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    master_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("masters.id"), nullable=False)
    service_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("services.id"), nullable=False)

    master: Mapped["Master"] = relationship("Master", back_populates="services")
    service: Mapped["Service"] = relationship("Service", back_populates="masters")

    __table_args__ = (
        Index("ix_master_services_master_service", "master_id", "service_id", unique=True),
    )


class Slot(Base):
    __tablename__ = "slots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    master_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("masters.id"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    booking_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bookings.id"), nullable=True
    )

    master: Mapped["Master"] = relationship("Master", back_populates="slots")
    booking: Mapped[Optional["Booking"]] = relationship("Booking", back_populates="slots")

    __table_args__ = (
        Index("ix_slots_master_starts_at", "master_id", "starts_at"),
        Index("ix_slots_booking_id", "booking_id"),
    )


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    master_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("masters.id"), nullable=False)
    service_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("services.id"), nullable=False)
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus), default=BookingStatus.confirmed, nullable=False
    )
    price_at_booking: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    reminder_24h_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reminder_2h_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    client: Mapped["Client"] = relationship("Client", back_populates="bookings")
    master: Mapped["Master"] = relationship("Master", back_populates="bookings")
    service: Mapped["Service"] = relationship("Service", back_populates="bookings")
    slots: Mapped[List["Slot"]] = relationship("Slot", back_populates="booking")
    review: Mapped[Optional["Review"]] = relationship("Review", back_populates="booking")

    __table_args__ = (
        Index("ix_bookings_client_status", "client_id", "status"),
        Index(
            "ix_bookings_pending_24h_reminder",
            "id",
            postgresql_where="status = 'confirmed' AND reminder_24h_sent = FALSE",
        ),
    )


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bookings.id"), nullable=False, unique=True
    )
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    booking: Mapped["Booking"] = relationship("Booking", back_populates="review")
    client: Mapped["Client"] = relationship("Client", back_populates="reviews")
