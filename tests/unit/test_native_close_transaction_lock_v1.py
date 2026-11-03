"""Actual local SQLAlchemy transaction mechanics, never native PG authority."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.trade_qualification import native_controlled_close_v1 as close


def actual_local_transaction():
    # Actual SQLite in-memory Connection/RootTransaction under the SAME
    # installed SQLAlchemy. No PostgreSQL role/UID/Source lock is fabricated.
    engine = create_engine("sqlite:///:memory:")
    session = AsyncSession()
    session.sync_session.bind = engine
    session.sync_session.begin()
    connection = session.sync_session.connection()
    held = {
        "transaction": session.sync_session.get_transaction(),
        "connection": connection,
        "connection_transaction": connection.get_transaction(),
    }
    return engine, session, held


@pytest.mark.parametrize(
    "fault",
    (
        "root_rollback",
        "connection_close",
        "invalidate",
        "session_rollback",
        "session_nested",
        "connection_nested",
    ),
)
def test_actual_physical_transaction_loss_denies_even_with_session_facade(fault):
    engine, session, held = actual_local_transaction()
    try:
        close._require_actual_close_connection_v1(held, session, engine)
        if fault == "root_rollback":
            held["connection_transaction"].rollback()
            # Demonstrates the reviewed defect: facade still looks present,
            # while the actual PostgreSQL-equivalent root lock owner is gone.
            assert session.in_transaction() and held["transaction"].is_active
        elif fault == "connection_close":
            held["connection"].close()
            assert session.in_transaction()
        elif fault == "invalidate":
            held["connection"].invalidate()
            assert session.in_transaction()
        elif fault == "session_rollback":
            session.sync_session.rollback()
        elif fault == "session_nested":
            session.sync_session.begin_nested()
        else:
            held["connection"].begin_nested()
        with pytest.raises(close.NativeControlledCloseError):
            close._require_actual_close_connection_v1(held, session, engine)
    finally:
        session.sync_session.close()
        engine.dispose()


def test_new_root_transaction_does_not_replace_retained_original_identity():
    engine, session, held = actual_local_transaction()
    try:
        old_transaction = held["transaction"]
        session.sync_session.rollback()
        session.sync_session.begin()
        session.sync_session.connection()
        assert session.sync_session.get_transaction() is not old_transaction
        with pytest.raises(close.NativeControlledCloseError):
            close._require_actual_close_connection_v1(held, session, engine)
    finally:
        session.sync_session.close()
        engine.dispose()
