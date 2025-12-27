from sqlalchemy import Column, Integer, String, Date, Time, Boolean, ForeignKey, Text
from sqlalchemy.orm import relationship
from db import Base

class Specialty(Base):
    __tablename__ = "specialties"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    color_hex = Column(String, nullable=False, default="#F5C542")

class Staff(Base):
    __tablename__ = "staff"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)

class Salon(Base):
    __tablename__ = "salons"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)  # "Salon 1", "Salon 2"

class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=True, default="")
    email = Column(String, nullable=True, default="")
    notes = Column(Text, nullable=True, default="")      # notas generales
    last_visit = Column(Date, nullable=True)            # última visita

    appointments = relationship("Appointment", back_populates="client")

class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True, index=True)

    date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    duration_min = Column(Integer, nullable=False, default=30)

    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    specialty_id = Column(Integer, ForeignKey("specialties.id"), nullable=True)
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=True)
    salon_id = Column(Integer, ForeignKey("salons.id"), nullable=True)

    deposit_paid = Column(Boolean, nullable=False, default=False)
    deposit_amount = Column(Integer, nullable=False, default=0)

    notes = Column(Text, nullable=True, default="")     # notas del turno

    status = Column(String, nullable=False, default="ACTIVO")  # ACTIVO / CANCELADO

    client = relationship("Client", back_populates="appointments")
    specialty = relationship("Specialty")
    staff = relationship("Staff")
    salon = relationship("Salon")
