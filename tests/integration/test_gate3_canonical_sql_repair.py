"""Disposable PostgreSQL acceptance for the already-applied 0030 SQL repair.

The old ambiguous function is restored inside an isolated test database to
exercise the forward migration, never in a user or production database.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.test_gate3_canonical_schedule_claim import (
    isolated_claim_database,  # noqa: F401 - pytest discovers imported fixture
    migrate,
)
from tests.integration.test_gate3_committed_preregistration_seal import (
    isolated_prereg_database,  # noqa: F401 - pytest discovers imported fixture
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


_DEPLOYED_AMBIGUOUS_ASCII = """
  CREATE OR REPLACE FUNCTION public.gate3_schedule_all_ascii(p jsonb)
  RETURNS boolean AS $$
  DECLARE kind text; member record; value text;
  BEGIN
    kind := pg_catalog.jsonb_typeof(p);
    IF kind = 'string' THEN
      value := p #>> '{}';
      RETURN pg_catalog.octet_length(value) = pg_catalog.length(value);
    ELSIF kind = 'object' THEN
      FOR member IN SELECT key,value FROM pg_catalog.jsonb_each(p) LOOP
        IF pg_catalog.octet_length(member.key) <> pg_catalog.length(member.key)
          OR NOT public.gate3_schedule_all_ascii(member.value)
        THEN RETURN false; END IF;
      END LOOP;
      RETURN true;
    ELSIF kind = 'array' THEN
      FOR member IN SELECT value FROM pg_catalog.jsonb_array_elements(p) LOOP
        IF NOT public.gate3_schedule_all_ascii(member.value) THEN
          RETURN false;
        END IF;
      END LOOP;
      RETURN true;
    END IF;
    RETURN kind IN ('number','boolean','null');
  END; $$ LANGUAGE plpgsql IMMUTABLE STRICT
    SET search_path=pg_catalog,pg_temp
"""


async def _function_identities(connection):
    return (
        await connection.execute(
            text("""
              SELECT p.proname,p.oid::text,p.proowner::text,
                     coalesce(p.proacl::text,'') AS acl,
                     p.prosecdef,p.proisstrict,p.provolatile,
                     pg_catalog.array_to_string(p.proconfig,',') AS config
              FROM pg_catalog.pg_proc p
              WHERE p.oid IN (
                'public.gate3_schedule_all_ascii(jsonb)'::pg_catalog.regprocedure,
                'public.gate3_prereg_integer_numbers(jsonb)'::pg_catalog.regprocedure)
              ORDER BY p.proname
            """)
        )
    ).all()


@pytest.mark.parametrize("isolated_claim_database", ["unknown"], indirect=True)
async def test_0031_repairs_deployed_sql_without_reclassifying_legacy_evidence(
    isolated_claim_database,  # noqa: F811 - fixture injected by pytest
):
    _, _, _, db_engine, _, _, _, _ = isolated_claim_database
    async with db_engine.begin() as connection:
        await connection.run_sync(migrate, "0030", "upgrade")
        # Emulate a DB that applied the old 0029 body before source correction.
        await connection.execute(text(_DEPLOYED_AMBIGUOUS_ASCII))
        before_identity = await _function_identities(connection)
        before_legacy = (
            await connection.execute(
                text("""
                  SELECT schedule_sha256,seal_sha256,window_key,holdout_id,
                         classification,observed_at
                  FROM public.gate3_capture_schedule_legacy_inventory
                  ORDER BY schedule_sha256
                """)
            )
        ).all()
        assert len(before_legacy) == 1
        assert before_legacy[0].classification == "unknown"
        for table in (
            "gate3_capture_schedule_key_claims",
            "gate3_capture_schedule_claim_acks",
            "gate3_preregistration_seals",
            "gate3_preregistration_seal_acks",
        ):
            assert (
                await connection.scalar(text(f"SELECT count(*) FROM public.{table}"))
                == 0
            )

    async with db_engine.connect() as connection:
        with pytest.raises(DBAPIError, match='column reference "value" is ambiguous'):
            await connection.scalar(
                text("SELECT public.gate3_schedule_all_ascii(CAST(:p AS jsonb))"),
                {"p": '{"nested":["ASCII",2]}'},
            )

    async with db_engine.begin() as connection:
        await connection.run_sync(migrate, "0031", "upgrade")
        assert await _function_identities(connection) == before_identity
        after_legacy = (
            await connection.execute(
                text("""
                  SELECT schedule_sha256,seal_sha256,window_key,holdout_id,
                         classification,observed_at
                  FROM public.gate3_capture_schedule_legacy_inventory
                  ORDER BY schedule_sha256
                """)
            )
        ).all()
        assert after_legacy == before_legacy
        for table in (
            "gate3_capture_schedule_key_claims",
            "gate3_capture_schedule_claim_acks",
            "gate3_preregistration_seals",
            "gate3_preregistration_seal_acks",
        ):
            assert (
                await connection.scalar(text(f"SELECT count(*) FROM public.{table}"))
                == 0
            )
        assert (
            await connection.scalar(
                text("SELECT public.gate3_schedule_all_ascii(CAST(:p AS jsonb))"),
                {"p": '{"nested":["ASCII",2]}'},
            )
            is True
        )
        assert (
            await connection.scalar(
                text("SELECT public.gate3_schedule_all_ascii(CAST(:p AS jsonb))"),
                {"p": '{"nested":["é",2]}'},
            )
            is False
        )
        assert (
            await connection.scalar(
                text("SELECT public.gate3_prereg_integer_numbers(CAST(:p AS jsonb))"),
                {"p": '{"nested":[0,-2,{"count":3}]}'},
            )
            is True
        )
        assert (
            await connection.scalar(
                text("SELECT public.gate3_prereg_integer_numbers(CAST(:p AS jsonb))"),
                {"p": '{"nested":[1.5]}'},
            )
            is False
        )

    with pytest.raises(
        DBAPIError, match="gate3_canonical_sql_repair_downgrade_requires_empty"
    ):
        async with db_engine.begin() as connection:
            await connection.run_sync(migrate, "0031", "downgrade")
    async with db_engine.connect() as connection:
        assert await _function_identities(connection) == before_identity
        assert (
            await connection.scalar(
                text("SELECT public.gate3_schedule_all_ascii(CAST(:p AS jsonb))"),
                {"p": '{"nested":["ASCII"]}'},
            )
            is True
        )


async def test_0031_empty_database_can_downgrade_and_reupgrade(
    isolated_prereg_database,  # noqa: F811 - fixture injected by pytest
):
    _, _, _, db_engine, _, _, _, _, _ = isolated_prereg_database
    async with db_engine.begin() as connection:
        await connection.run_sync(migrate, "0031", "upgrade")
    async with db_engine.begin() as connection:
        original_identity = await _function_identities(connection)
        assert (
            await connection.scalar(
                text("SELECT public.gate3_schedule_all_ascii(CAST(:p AS jsonb))"),
                {"p": '{"nested":["ASCII"]}'},
            )
            is True
        )
        await connection.run_sync(migrate, "0031", "downgrade")
    async with db_engine.begin() as connection:
        assert await _function_identities(connection) == original_identity
        # Even at the 0030 step, the safe predicate remains installed; the
        # subsequent 0030/0029 downgrades remove their own functions.
        assert (
            await connection.scalar(
                text("SELECT public.gate3_schedule_all_ascii(CAST(:p AS jsonb))"),
                {"p": '{"nested":["ASCII"]}'},
            )
            is True
        )
        await connection.run_sync(migrate, "0031", "upgrade")
    async with db_engine.connect() as connection:
        assert await _function_identities(connection) == original_identity
        assert (
            await connection.scalar(
                text("SELECT public.gate3_prereg_integer_numbers(CAST(:p AS jsonb))"),
                {"p": '{"nested":[1,2]}'},
            )
            is True
        )
