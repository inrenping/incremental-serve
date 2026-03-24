from sqlalchemy import create_engine, MetaData
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

engine = create_engine(settings.DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 指定 Schema
Base = declarative_base(metadata=MetaData(schema="blunt"))

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()