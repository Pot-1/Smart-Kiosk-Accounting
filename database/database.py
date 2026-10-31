from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

from config import DATABASE_URL
from database.models import Base


_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def initialize_database():
    Base.metadata.create_all(engine)
    adjustment_columns = {
        column["name"] for column in inspect(engine).get_columns("stock_adjustments")
    }
    if "total_cost_kobo" not in adjustment_columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE stock_adjustments "
                    "ADD COLUMN total_cost_kobo INTEGER NOT NULL DEFAULT 0"
                )
            )
    from database.seed_data import seed_demo_business

    with SessionLocal() as session:
        seed_demo_business(session)
        session.commit()


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
