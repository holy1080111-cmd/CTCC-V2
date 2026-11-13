"""Verify monitor HTTP and exact installed-source checks with restricted TLS PostgreSQL."""

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError

from app.config.settings import Settings
from app.core.native_only_lifespan import _MUST_BE_FALSE
from app.database import session as database
from app.database.models.demo_automation import DemoAutomationState
from app.database.repositories.public_runtime_bundle_custody import _closure
from app.domain.source_primitives import sha
from scripts import current0056_fitted_catalog_measurement_v1 as measurement
from scripts import install_native_startup_trust_0056_v1 as installer
from scripts import native_control_status_0056_v1 as control
from scripts import serve_native_control_monitor_0056 as monitor
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _actual_0056_role_observation_lab,  # noqa: F401
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_ARCHIVE = Path("/ctcc-admitted-test-source.tar")


async def test_actual0056_restricted_control_repository_reads_back_lifecycle(
    _actual_0056_role_observation_lab,  # noqa: F811
    monkeypatch,
):
    from app.core.native_role_witness_0056_v1 import (
        observe_restricted_native_roles_0056_witness_v1,
    )
    from app.database.repositories import demo_control_current56_v1 as repository56
    from app.database.repositories.demo_control import DemoControlRepository
    from app.trade_qualification.demo_control import (
        ControlPins,
        ControlScope,
        account_lock_key,
    )
    from app.trade_qualification.demo_control_runtime import DemoControlOwner

    owner_engine, owner_factory, selected, _ = _actual_0056_role_observation_lab
    roles = await observe_restricted_native_roles_0056_witness_v1(
        selected, source=control.deployment._SOURCE
    )
    scope = ControlScope("demo", "730021")

    def clock():
        return datetime.now(UTC)

    historical = DemoControlRepository(owner_factory, clock=clock)
    genesis = await historical.latch_stop(scope, command_id="a" * 64)
    current = repository56.DemoControlRepository0056V1(
        selected, roles=roles, scope=scope, clock=clock
    )
    assert (await current.verify_native_deployment_v2()).state == genesis.state
    pending_sessions = []
    original = repository56.DemoControlRepository._store

    async def observe_actual_lock(session, row, state, **write):
        pending_sessions.append(
            (state.revision, (await repository56.lineage._metadata(session))[8])
        )
        if state.revision == 2:
            async with owner_engine.begin() as competing:
                assert not await competing.scalar(
                    text("SELECT pg_catalog.pg_try_advisory_xact_lock(:key)"),
                    {"key": account_lock_key(scope)},
                )
                with pytest.raises(DBAPIError) as ddl:
                    await competing.exec_driver_sql(
                        "LOCK TABLE public.demo_control_journal IN ACCESS EXCLUSIVE MODE NOWAIT"
                    )
                assert ddl.value.orig.sqlstate == "55P03"
        return await original(session, row, state, **write)

    committed_sessions = []
    original_read = current._read

    async def observe_actual_readback(exact_scope):
        result = await original_read(exact_scope)
        committed_sessions.append((result.state.revision, result.metadata[8]))
        return result

    with monkeypatch.context() as observation:
        observation.setattr(
            repository56.DemoControlRepository,
            "_store",
            staticmethod(observe_actual_lock),
        )
        observation.setattr(current, "_read", observe_actual_readback)
        pins = ControlPins("b" * 64, "c" * 64, "d" * 64)
        owner = DemoControlOwner(current, scope, pins)
        acquired = await owner.start()
        renewed = await owner.renew()
        assert acquired.state.owner_epoch == renewed.state.owner_epoch == 1
        assert acquired.state.emergency_stop is True
        assert renewed.state.owner_sha256 == owner.owner_sha256
        assert not owner.observed_arm_intent and not renewed.execution_authority
        with pytest.raises(repository56.DemoControlRepository0056Error):
            await current.change(
                scope,
                owner_token=b"g" * 32,
                owner_epoch=1,
                expected_revision=renewed.state.revision,
                action="arm_requested",
                command_id="e" * 64,
                arm_expires_at=clock() + timedelta(seconds=1),
            )
        with pytest.raises(repository56.DemoControlRepository0056Error):
            await current.clear_stop(scope, passed=True)
        with pytest.raises(repository56.DemoControlRepository0056Error):
            await current.change(
                scope,
                owner_token=owner._owner_token,
                owner_epoch=1,
                expected_revision=acquired.state.revision,
                action="renew",
                command_id="e" * 64,
            )
        assert (await original_read(scope)).event == renewed.event_sha256
        disarmed = await owner.disarm()
        released = await owner.release()
        stop = await current.latch_stop(scope, command_id="f" * 64)
        assert (
            disarmed.state.arm_request_id
            is released.state.arm_request_id
            is stop.state.arm_request_id
            is None
        )
        assert stop.state.emergency_stop is True
    assert [rev for rev, _ in pending_sessions] == [2, 3, 4, 5, 6]
    assert [rev for rev, _ in committed_sessions] == [2, 3, 4, 5, 6]
    assert len({tx for _, tx in pending_sessions + committed_sessions}) == 10
    assert (await current.read(scope)).event_sha256 == stop.event_sha256
    with pytest.raises(repository56.DemoControlRepository0056Error):
        await current.read(ControlScope("demo", "730022"))

    async def lost_readback(_scope):
        raise RuntimeError("simulated readback loss after durable commit")

    with monkeypatch.context() as observation:
        observation.setattr(current, "_read", lost_readback)
        with pytest.raises(repository56.DemoControlRepository0056Uncertain):
            await current.latch_stop(scope, command_id="0" * 64)
    reopened = await current.read(scope)
    assert reopened.state.revision == stop.state.revision + 1
    assert reopened.state.emergency_stop is True


async def test_actual0056_control_monitor_joins_exact_release_and_restricted_tls(
    _actual_0056_role_observation_lab,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    """Compose the real layers with only the OS reference lookup injected."""
    _, owner, selected, _ = _actual_0056_role_observation_lab
    identity, files, source_map = measurement._read_hermetic_inputs()
    source = measurement.source_identity(identity, files, source_map)
    assert _ARCHIVE.is_file()
    assert sha(_ARCHIVE.read_bytes()) == identity["archive_sha256"]
    deployment = control.deployment
    current = await deployment.identity.observe_restricted_role_identity_0056_v2(
        selected, source=deployment._SOURCE
    )
    reference = deployment.load_reviewed_owner_portable_0056_v1(deployment._SOURCE)
    ca_file = os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE"]
    ca_sha = os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"]
    descriptor = deployment._Descriptor(
        source_commit=source["commit"],
        source_tree=source["tree"],
        source_archive_sha256=identity["archive_sha256"],
        source_manifest_sha256=source["manifest_sha256"],
        tls_ca_sha256=ca_sha,
        restricted_role_identity_sha256=current.stable_identity_sha256,
        inherited_native_definitions_sha256=current.inherited_native_definitions_sha256,
        granted_inherited_acl_shape_sha256=current.granted_inherited_acl_shape_sha256,
        reviewed_portable_reference_sha256=reference.reference_sha256,
        owner_observed_cluster_sha256=sha(b"isolated-fixture-owner"),
        owner_grant_plan_sha256=sha(b"isolated-fixture-grants"),
    )
    private = tmp_path / "control-monitor"
    private.mkdir(mode=0o700)
    descriptor_sha = deployment._publish_descriptor(private, descriptor)
    anchor = control.trust._parse(
        installer._reference_document(descriptor, descriptor_sha)
    )
    monkeypatch.setattr(
        control.trust, "read_native_startup_trust_0056_v1", lambda: anchor
    )
    factories = (
        selected.writer_factory,
        selected.custody_factory,
        selected.frame_reader_factory,
    )
    urls = [f.kw["bind"].url.render_as_string(hide_password=False) for f in factories]
    token = "q" * 48
    settings = Settings(
        _env_file=None,
        environment="production",
        ctcc_native_only_lifespan_v1=True,
        trading_mode="okx_demo",
        okx_demo_enabled=True,
        api_token=token,
        database_url=urls[0],
        okx_demo_native_custody_database_url=urls[1],
        okx_demo_native_frame_reader_database_url=urls[2],
        ctcc_native_pg_tls_ca_file=ca_file,
        ctcc_native_pg_tls_ca_sha256=ca_sha,
        **dict.fromkeys(_MUST_BE_FALSE, False),
    )
    # Preserve the real configured selector and its DSN/CA checks while
    # pointing the application configuration at the isolated three-role fixture.
    monkeypatch.setattr(deployment, "get_settings", lambda: settings)
    monkeypatch.setattr(deployment.selectors, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "settings", settings)
    monkeypatch.setattr(database, "AsyncSessionFactory", factories[0])
    monkeypatch.setattr(database, "engine", factories[0].kw["bind"])
    monkeypatch.setattr(
        database, "native_pg_tls_settings_pin", (urls[0], ca_file, ca_sha)
    )
    monkeypatch.setattr(
        database,
        "native_pg_tls_context",
        _closure(factories[0].kw["bind"].sync_engine.pool._creator)["cparams"]["ssl"],
    )
    instant = datetime(2026, 1, 1, tzinfo=UTC)
    async with owner.begin() as session:
        session.add(
            DemoAutomationState(
                id=1,
                control_revision=7,
                session_date=instant.date(),
                armed=False,
                restart_latch_required=True,
                updated_at=instant,
            )
        )
    app = monitor.build_native_control_monitor_0056(
        descriptor_file=private / deployment._LEAF,
        source_archive_file=_ARCHIVE,
        port=8126,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8126",
        ) as client,
    ):
        endpoint = "/api/demo/control"
        assert (await client.get(endpoint)).status_code == 401
        assert (await client.post(endpoint)).status_code == 405
        read = await client.get(endpoint, headers={"X-CTCC-Token": token})
        assert read.status_code == 200
        assert read.json()["source_commit"] == source["commit"]
        assert read.json()["deployment_admin_anchor_sha256"] == anchor.trust_sha256
        assert read.json()["control_revision"] == 7
        assert read.json()["monitoring_only"] is True
        assert read.json()["execution_authority"] is False
        assert read.json()["order_writes"] == 0
        async with owner.begin() as session:
            await session.execute(
                update(DemoAutomationState).values(control_revision=8)
            )
        reread = await client.get(endpoint, headers={"X-CTCC-Token": token})
        assert reread.status_code == 200 and reread.json()["control_revision"] == 8
        changed = replace(anchor, descriptor_sha256=sha(b"different-descriptor"))
        monkeypatch.setattr(
            control.trust, "read_native_startup_trust_0056_v1", lambda: changed
        )
        for _ in range(2):
            refusal = await client.get(endpoint, headers={"X-CTCC-Token": token})
            assert refusal.status_code == 503
            assert refusal.json() == {"detail": "native_control_monitor_unavailable"}
    async with owner() as session:
        row = await session.get(DemoAutomationState, 1)
        assert row.control_revision == 8 and row.armed is False
