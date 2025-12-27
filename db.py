import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

IS_RENDER = os.getenv("RENDER") is not None

# IMPORTANTE: nombre nuevo de DB en Render para arrancar limpio
if IS_RENDER:
    DATABASE_URL = "sqlite:////var/data/angies_color.db"
else:
    DATABASE_URL = "sqlite:///./angies_color.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
