"""One engine and session factory for the "Ai" database (the same one task management's Design module uses)."""
import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config

logger = logging.getLogger(__name__)

_url = config.database_url()
if _url.startswith("sqlite"):
    engine = create_engine(_url, connect_args={"check_same_thread": False})
else:
    engine = create_engine(_url, pool_size=10, max_overflow=10, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
