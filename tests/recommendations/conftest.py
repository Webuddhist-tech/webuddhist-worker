import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from worker_api.recommendations.models import (
    RecDismissal,
    RecExposure,
    RecItem,
    RecMembership,
    RecMembershipChange,
    RecProcessedEvent,
)

_TABLES = [
    model.__table__
    for model in (
        RecDismissal,
        RecExposure,
        RecItem,
        RecMembership,
        RecMembershipChange,
        RecProcessedEvent,
    )
]


@pytest.fixture
def db_session():
    """Only the recommendation tables: the shared Base also holds models with
    foreign keys to backend tables the worker never creates."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in _TABLES:
        table.create(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
