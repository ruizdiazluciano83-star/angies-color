from sqlalchemy import Column, Integer, String, Date, Time, Boolean, ForeignKey, Text
from sqlalchemy.orm import relationship
from db import Base


class Specialty(Base):
    __tablename__ = "specialties"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    color_hex = Column(String(12), default="#60a5fa")  # default azul suave

    appointments = relationship("Appointment", back_populates="specialty")


class Staff(Base):
    __tablename__ = "staff"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)

    appointments = relationship("Appointment", back_populates="staff")


class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(180), nullable=False)
    phone = Column(String(40), default="")
    email = Column(String(180), default="")
    notes = Column(Text, default="")

    # ✅ última visita (se actualiza cuando guardás un turno)
    last_visit = Column(Date, nullable=True)

    appointments = relationship("Appointment", back_populates="client")


class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True, index=True)

    date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    duration_min = Column(Integer, default=30)

    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    specialty_id = Column(Integer, ForeignKey("specialties.id"), nullable=True)
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=True)

    # 1 o 2 (fijo por ahora)
    salon = Column(Integer, default=1)

    deposit_paid = Column(Boolean, default=False)
    deposit_amount = Column(Integer, default=0)

    notes = Column(Text, default="")
    status = Column(String(30), default="ACTIVO")  # ACTIVO / CANCELADO

    # WhatsApp
    wa_sent = Column(Boolean, default=False)

    # ✅ relaciones prolijas
    client = relationship("Client", back_populates="appointments")
    specialty = relationship("Specialty", back_populates="appointments")
    staff = relationship("Staff", back_populates="appointments")
