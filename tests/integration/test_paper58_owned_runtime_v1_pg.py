"""Actual fresh PG58 TLS role, restricted HTTP mutations, and restart recovery."""

import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from app.api.paper58_owned_http_v1 import build_owned_paper58_app_v1
from app.core.owned_paper58_runtime_v1 import Paper58RuntimeRefused
from scripts.paper58_actor_inventory_sql_v1 import CONTROL_ACL_SQL
from scripts.paper58_owner_preflight_v1 import (
    capture_fresh_unpinned_preprovision_owner_v1,
)
from scripts.paper58_source_coverage_v1 import TABLE_PRIVILEGES
from tests.integration import test_paper58_catalog_reference_v1_pg as original

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_canonical_fresh55 = original._canonical_fresh55
_PORT = 18058
_BASE = f"http://127.0.0.1:{_PORT}"


@pytest.fixture
async def _fresh58(_canonical_fresh55, record_property):
    owner, _ = _canonical_fresh55
    await original._native55_predecessor(owner)
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            "SET LOCAL search_path TO public, pg_catalog, pg_temp"
        )
        await connection.run_sync(original._upgrade_original55_to58)
    async with owner.begin() as connection:
        for statement in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='30000ms'",
        ):
            await connection.exec_driver_sql(statement)
        _, measured = await capture_fresh_unpinned_preprovision_owner_v1(connection)
        assert measured["preprovision_catalog_matches"] is True
    actor = f"ctcc_paper58_ci_{uuid4().hex[:16]}"
    async with owner.begin() as connection:
        before = (await connection.execute(text(CONTROL_ACL_SQL))).mappings().one()
        assert before["changed_from_initial"] is False
        assert before["public_execute"] is before["initial_public_execute"]
        assert type(before["public_execute"]) is bool
        await connection.exec_driver_sql(
            "REVOKE EXECUTE ON FUNCTION pg_catalog.pg_control_system() FROM PUBLIC"
        )
        after = (await connection.execute(text(CONTROL_ACL_SQL))).mappings().one()
        assert after["changed_from_initial"] is before["public_execute"]
        assert after["owner_only"] is True and after["public_execute"] is False
        record_property(
            "native_control_public_execute_before", before["public_execute"]
        )
        record_property("native_control_public_execute_after", after["public_execute"])
        await connection.exec_driver_sql(
            f'CREATE ROLE "{actor}" LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE '
            "NOCREATEDB NOREPLICATION NOBYPASSRLS"
        )
        await connection.exec_driver_sql(
            f'REVOKE TEMPORARY ON DATABASE "{owner.url.database}" FROM PUBLIC'
        )
        await connection.exec_driver_sql(
            f'GRANT CONNECT ON DATABASE "{owner.url.database}" TO "{actor}"'
        )
        await connection.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        await connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{actor}"')
        await connection.exec_driver_sql(
            "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC"
        )
        for table, rights in TABLE_PRIVILEGES:
            assert table.isascii() and table.replace("_", "").isalnum()
            assert set(rights) <= {"SELECT", "INSERT", "UPDATE", "DELETE"}
            await connection.exec_driver_sql(
                f'GRANT {", ".join(rights)} ON TABLE public."{table}" TO "{actor}"'
            )
    try:
        yield owner, actor
    finally:
        async with owner.begin() as connection:
            await connection.exec_driver_sql(f'DROP OWNED BY "{actor}"')
            await connection.exec_driver_sql(f'DROP ROLE "{actor}"')


def _order(label, **extra):
    return {
        "symbol": "BTC-USDT-SWAP",
        "side": "long",
        "quantity": "1",
        "reference_price": "100",
        "stop_loss": "90",
        "take_profit": "120",
        "client_order_id": label,
    } | extra


async def test_actual58_owned_paper_http_recovers_real_restricted_state_after_commit_ack_fault(
    _fresh58, monkeypatch
):
    owner, actor = _fresh58
    token = uuid4().hex + uuid4().hex
    settings = {
        "owner_database_url": owner.url.render_as_string(hide_password=False),
        "actor_database_url": owner.url.set(username=actor).render_as_string(
            hide_password=False
        ),
        "ca_file": Path(os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE"]),
        "ca_sha256": os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"],
        "port": _PORT,
        "token": token,
    }
    app = build_owned_paper58_app_v1(**settings)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=transport, base_url=_BASE, trust_env=False
        ) as client,
    ):
        runtime = app.state.owned_paper58_runtime
        assert (
            runtime.recovery.consistent is True and runtime.recovery.last_error is None
        )
        assert runtime.recovery.recovered is False
        assert (
            runtime.service.repository.session_factory.kw["bind"] is runtime._connection
        )
        async with runtime.service.repository.session_factory() as session:
            assert await session.scalar(text("SELECT current_user")) == actor
        assert (await client.get("/readiness")).status_code == 200
        assert (await client.get("/dashboard/local-analysis")).status_code == 200
        assert (await client.get("/api/paper/account")).status_code == 401
        assert (
            await client.get(
                "/api/paper/state", headers={"Origin": "https://unrelated.invalid"}
            )
        ).status_code == 403
        assert (
            await client.post("/api/paper/reset", headers={"X-CTCC-Token": token})
        ).status_code == 404
        client.headers["X-CTCC-Token"] = token
        empty = (await client.get("/api/paper/runtime/state")).json()
        assert not empty["state"]["orders"] and not empty["state"]["positions"]
        client.headers["X-CTCC-Paper-Runtime"] = empty["runtime_id"]
        market = await client.post("/api/paper/orders", json=_order("market-test"))
        assert market.status_code == 201 and market.json()["status"] == "filled"
        limit = await client.post(
            "/api/paper/orders",
            json=_order("limit-test", order_type="limit", limit_price="95"),
        )
        assert limit.status_code == 201 and limit.json()["status"] == "pending"
        tick = await client.post(
            "/api/paper/ticks", json={"symbol": "BTC-USDT-SWAP", "price": "103"}
        )
        assert tick.status_code == 200
        limit_id = limit.json()["id"]
        cancelled = await client.post(f"/api/paper/orders/{limit_id}/cancel")
        assert (
            cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        )
        state = (await client.get("/api/paper/state")).json()
        position_id = state["positions"][0]["id"]
        closed = await client.post(
            f"/api/paper/positions/{position_id}/close", json={"price": "104"}
        )
        assert closed.status_code == 200 and closed.json()["status"] == "closed"
        assert (
            await client.get(f"/api/paper/orders/{market.json()['id']}")
        ).status_code == 200
        assert (
            await client.get(f"/api/paper/positions/{position_id}")
        ).status_code == 200
        competing = build_owned_paper58_app_v1(**settings)
        with pytest.raises(
            Paper58RuntimeRefused, match="paper58_runtime_startup_refused"
        ):
            async with competing.router.lifespan_context(competing):
                pytest.fail("a second paper process acquired the held lease")

        # The database receives its real COMMIT. Simulate losing the response
        # immediately afterward on this runtime's distinct dialect only.
        actual_commit = runtime._connection.dialect.do_commit

        def acknowledgement_lost(dbapi_connection):
            actual_commit(dbapi_connection)
            raise OSError("isolated test commit acknowledgement lost")

        with monkeypatch.context() as fault:
            fault.setattr(
                runtime._connection.dialect, "do_commit", acknowledgement_lost
            )
            ambiguous = await client.post(
                "/api/paper/orders", json=_order("ambiguous-test")
            )
        assert ambiguous.status_code == 503
        assert ambiguous.json() == {"detail": "paper_persistence_outcome_unknown"}
        assert (await client.get("/readiness")).status_code == 503
        assert (
            await client.post("/api/paper/orders", json=_order("blocked-after-unknown"))
        ).status_code == 503

    assert runtime._connection.closed
    restarted = build_owned_paper58_app_v1(**settings)
    async with (
        restarted.router.lifespan_context(restarted),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted),
            base_url=_BASE,
            trust_env=False,
            headers={"X-CTCC-Token": token},
        ) as client,
    ):
        recovered = restarted.state.owned_paper58_runtime
        assert recovered._connection is not runtime._connection
        assert (
            recovered.recovery.recovered is True
            and recovered.recovery.consistent is True
        )
        assert recovered.recovery.last_error is None
        result = (await client.get("/api/paper/runtime/state")).json()
        assert result["runtime_id"] != empty["runtime_id"]
        assert result["installation_binding"] == empty["installation_binding"]
        assert {
            order["client_order_id"]: order["status"]
            for order in result["state"]["orders"]
        } == {
            "market-test": "filled",
            "limit-test": "cancelled",
            "ambiguous-test": "filled",
        }
        stale = await client.post(
            "/api/paper/orders",
            json=_order("old-generation-test"),
            headers={"X-CTCC-Paper-Runtime": empty["runtime_id"]},
        )
        assert (
            stale.status_code == 409
            and stale.json()["detail"] == "paper_runtime_changed"
        )
