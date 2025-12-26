from sqlalchemy import Column, Integer, String, Date, Time, ForeignKey, Boolean, Text, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from db import Base

class Specialty(Base):
    __tablename__ = "specialties"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    duration_min = Column(Integer, default=0)
    color_hex = Column(String, default="#16a34a", nullable=False)

    appointments = relationship("Appointment", back_populates="specialty")


class Staff(Base):
    __tablename__ = "staff"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)

    appointments = relationship("Appointment", back_populates="staff")


class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, default="")
    email = Column(String, default="")
    notes = Column(Text, default="")

    appointments = relationship("Appointment", back_populates="client")
    note_items = relationship("ClientNote", back_populates="client", cascade="all, delete-orphan")


class ClientNote(Base):
    __tablename__ = "client_notes"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    text = Column(Text, nullable=False)

    client = relationship("Client", back_populates="note_items")


class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True, index=True)

    date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    duration_min = Column(Integer, nullable=False)

    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    specialty_id = Column(Integer, ForeignKey("specialties.id"), nullable=False)
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=True)

    notes = Column(Text, default="")
    status = Column(String, default="CONFIRMADO")

    deposit_paid = Column(Boolean, default=False)
    deposit_amount = Column(Integer, default=0)

    # ✅ NUEVO: control de recordatorio WhatsApp
    reminder_sent = Column(Boolean, default=False)
    reminder_sent_at = Column(DateTime, nullable=True)

    client = relationship("Client", back_populates="appointments")
    specialty = relationship("Specialty", back_populates="appointments")
    staff = relationship("Staff", back_populates="appointments")
