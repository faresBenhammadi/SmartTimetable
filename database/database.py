from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///school.db")

# Use connection pooling for PostgreSQL; SQLite doesn't support it
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL)
else:
    engine = create_engine(
        DATABASE_URL,
        pool_size=5,          # Keep 5 persistent connections open
        max_overflow=10,      # Allow up to 10 extra connections under load
        pool_pre_ping=True,   # Verify connection is alive before using it
        pool_recycle=300,     # Recycle connections every 5 min to avoid timeouts
    )

SessionLocal = sessionmaker(bind=engine)

Base = declarative_base()