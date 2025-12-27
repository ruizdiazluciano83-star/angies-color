import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# En Render vamos a usar un disco persistente montado en /var/data
# En tu PC sigue usando glam_agenda.db en la carpeta del proyecto
IS_RENDER = os.getenv("RENDER") == "true"

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
