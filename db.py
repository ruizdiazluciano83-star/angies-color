import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Render define variables propias. Usamos eso para detectar producción.
IS_RENDER = os.getenv("RENDER") is not None

if IS_RENDER:
    DATABASE_URL = "sqlite:////var/data/glam_agenda.db"
else:
    DATABASE_URL = "sqlite:///./glam_agenda.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
