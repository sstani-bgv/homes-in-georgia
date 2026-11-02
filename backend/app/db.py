from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    # used as Depends(get_db, scope="function"): closed when the endpoint returns, not after the response is sent,
    # so a slow download (photos over a tunnel/mobile) never holds a pool connection
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
