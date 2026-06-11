import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from rfq_engine.engine import RfqEngine
from rfq_engine.models import Base

DATABASE_URL = os.environ["DATABASE_URL"]
FIXED_AT = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(DATABASE_URL)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db_session(engine):
    session = Session(engine)
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def engine_svc(db_session):
    return RfqEngine(db_session, FIXED_AT)


@pytest.fixture
def participants(engine_svc):
    requester = engine_svc.create_participant("requester", Decimal("10000"))
    mm1 = engine_svc.create_participant("mm_alpha", Decimal("10000"))
    mm2 = engine_svc.create_participant("mm_beta", Decimal("10000"))
    engine_svc.session.flush()
    return {"requester": requester, "mm1": mm1, "mm2": mm2}
