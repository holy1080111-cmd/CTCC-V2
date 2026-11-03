"""Real TEMP PostgreSQL uniqueness primitive; never native source admission."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain import native_close_intent_schema_v55 as schema
from app.domain.source_primitives import canonical
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- Pytest collects this imported fixture.
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_actual_unique_index_allows_one_close_intent_per_reservation(database):
    async with database() as session, session.begin():
        await session.execute(
            text(
                "CREATE TEMP TABLE qualification_submission_outcomes "
                "(reservation_id text NOT NULL,capture_json text NOT NULL) ON COMMIT DROP"
            )
        )
        (sql,) = schema.statements()
        assert sql.count("public.qualification_submission_outcomes") == 1
        await session.execute(
            text(
                sql.replace(
                    "public.qualification_submission_outcomes",
                    "pg_temp.qualification_submission_outcomes",
                )
            )
        )
        insert = text(
            "INSERT INTO pg_temp.qualification_submission_outcomes VALUES (:reservation,:capture)"
        )
        legacy = canonical({"version": "ctcc.native_read_raw_part.v1"}).decode()
        close = canonical(
            {
                "version": "ctcc.native_controlled_close_intent.v1",
                "close_client_order_id": "CTC123",
            }
        ).decode()
        for _ in range(2):
            await session.execute(insert, {"reservation": "a", "capture": legacy})
        await session.execute(insert, {"reservation": "a", "capture": close})
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(insert, {"reservation": "a", "capture": close})
        await session.execute(insert, {"reservation": "b", "capture": close})
        count = await session.scalar(
            text(
                "SELECT count(*) FROM pg_temp.qualification_submission_outcomes "
                "WHERE capture_json::jsonb->>'version'='ctcc.native_controlled_close_intent.v1'"
            )
        )
        assert count == 2


async def test_close_version_cannot_be_inserted_twice_with_different_json(database):
    async with database() as session, session.begin():
        await session.execute(
            text(
                "CREATE TEMP TABLE qualification_submission_outcomes "
                "(reservation_id text NOT NULL,capture_json text NOT NULL) ON COMMIT DROP"
            )
        )
        (sql,) = schema.statements()
        await session.execute(
            text(
                sql.replace(
                    "public.qualification_submission_outcomes",
                    "pg_temp.qualification_submission_outcomes",
                )
            )
        )
        insert = text(
            "INSERT INTO pg_temp.qualification_submission_outcomes VALUES ('a',:capture)"
        )
        await session.execute(
            insert,
            {"capture": '{"version":"ctcc.native_controlled_close_intent.v1","x":1}'},
        )
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    insert,
                    {
                        "capture": '{"x":2,"version":"ctcc.native_controlled_close_intent.v1"}'
                    },
                )
