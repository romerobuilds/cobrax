import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://cobrax:cobrax123@localhost:5432/cobrax"
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,          # <- testa conexão antes de usar
    pool_recycle=1800,           # <- recicla conexões velhas (30min)
    pool_size=5,
    max_overflow=10,
    connect_args={"connect_timeout": 5},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,    # testa a conexão antes de usar
    pool_recycle=1800,     # recicla conexão velha (30 min)
    pool_size=5,
    max_overflow=10,
)


