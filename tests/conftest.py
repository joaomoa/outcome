import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from rfq_engine.engine import RfqEngine

DATABASE_URL = os.environ["DATABASE_URL"]
FIXED_AT = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
SCHEMA = (Path(__file__).parent.parent / "schema.sql").read_text()


@pytest.fixture(scope="session")
def db():
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    conn.execute("DROP SCHEMA public CASCADE")
    conn.execute("CREATE SCHEMA public")
    conn.execute(SCHEMA)
    conn.close()


@pytest.fixture
def conn(db):
    # Outer BEGIN/ROLLBACK isolates tests only; engine methods commit via conn.transaction().
    connection = psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row)
    connection.execute("BEGIN")
    yield connection
    connection.rollback()
    connection.close()


@pytest.fixture
def engine(conn):
    return RfqEngine(conn, FIXED_AT)


@pytest.fixture
def participants(engine):
    requester = engine.create_participant("requester", Decimal("10000"))
    mm1 = engine.create_participant("mm_alpha", Decimal("10000"))
    mm2 = engine.create_participant("mm_beta", Decimal("10000"))
    return {"requester": requester, "mm1": mm1, "mm2": mm2}
