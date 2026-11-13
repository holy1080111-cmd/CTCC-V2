"""Verify monitor HTTP and exact installed-source checks with restricted TLS PostgreSQL."""

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

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


async def test_actual0056_coverage_replays_held_checkpoint_without_authority(
    _actual_0056_role_observation_lab,  # noqa: F811
):
    from app.core.native_role_witness_0056_v1 import (
        observe_restricted_native_roles_0056_witness_v1,
    )
    from app.database.repositories.demo_control import DemoControlRepository
    from app.database.repositories.qualification_ledger import (
        QualificationLedgerRepository,
    )
    from app.trade_qualification import (
        controlled_writer_coverage_current55_v2 as coverage,
    )
    from app.trade_qualification.demo_control import ControlScope, account_lock_key
    from app.trade_qualification.reservations import LedgerScope

    _, owner, selected, _ = _actual_0056_role_observation_lab
    factory = selected.writer_factory
    roles = await observe_restricted_native_roles_0056_witness_v1(
        selected, source=control.deployment._SOURCE
    )
    scope = LedgerScope(account_id="730066", settlement_currency="USDT")
    control_scope = ControlScope(scope.environment, scope.account_id)

    def clock():
        return datetime.now(UTC)

    await QualificationLedgerRepository(owner, clock=clock).initialize_capture_scope(
        scope
    )
    stopped = await DemoControlRepository(owner, clock=clock).latch_stop(
        control_scope, command_id=sha(b"ctcc isolated coverage stop")
    )
    repo = QualificationLedgerRepository(factory, clock=clock)
    snapshots = []
    for _ in range(2):
        async with factory() as writer, writer.begin():
            await writer.execute(
                text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE")
            )
            await writer.execute(text("SET LOCAL synchronous_commit='on'"))
            async with coverage._held_db0056_coverage_control(
                selected,
                roles,
                writer,
                control_scope,
                stopped.state,
                stopped.event_sha256,
            ) as held:
                async with owner.begin() as concurrent:
                    assert not await concurrent.scalar(
                        text("SELECT pg_catalog.pg_try_advisory_xact_lock(:key)"),
                        {"key": account_lock_key(control_scope)},
                    )
                row = await repo._locked(writer, scope)
                point = await repo._bootstrap_checkpoint(
                    writer, scope, row, _held0056=held
                )
                local = await repo._read_portfolio_checkpoint_locked(
                    writer, scope, row, _held0056=held
                )
                snapshots.append(
                    (
                        writer,
                        await writer.scalar(
                            text("SELECT pg_catalog.pg_current_xact_id()::text")
                        ),
                        point.state,
                        local.document_json,
                    )
                )
                assert held.control.execution_authority is False
                assert held.control.control.state.emergency_stop is True
    assert snapshots[0][0] is not snapshots[1][0]
    assert snapshots[0][1] != snapshots[1][1] and snapshots[0][2:] == snapshots[1][2:]
    assert b'"execution_authority":false' in snapshots[1][3]

    with pytest.raises(
        coverage.ControlledCoverageSourceErrorV2,
        match="controlled55_coverage_actual_control_changed",
    ):
        async with factory() as writer, writer.begin():
            await writer.execute(
                text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE")
            )
            await writer.execute(text("SET LOCAL synchronous_commit='on'"))
            async with coverage._held_db0056_coverage_control(
                selected, roles, writer, control_scope, stopped.state, "0" * 64
            ):
                pytest.fail("foreign Control event entered coverage")


async def test_actual0056_owner_initializes_legacy_singleton_only_as_stopped(
    _actual_0056_role_observation_lab,  # noqa: F811
):
    from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
    from app.core.native_role_witness_0056_v1 import (
        observe_restricted_native_roles_0056_witness_v1,
    )
    from app.database.models.demo_automation import DemoAutomationFingerprint
    from app.database.repositories import (
        native_legacy_control_genesis_0056_v1 as genesis,
    )
    from app.database.repositories.demo_control import DemoControlRepository
    from app.trade_qualification.demo_control import ControlScope
    from scripts import native_owner_postgrant_readback_0056_v1 as postgrant
    from scripts import native_owner_route_preflight_0056_v1 as owner_route

    owner_engine, owner_factory, selected, _ = _actual_0056_role_observation_lab
    owner = owner_engine.url
    async with owner_factory() as session, session.begin():
        profile = (await session.execute(owner_route._PROFILE_SQL)).one()
        actor = await roles56._identity_at_0056(session, owner.username)
    pins = owner_route.OwnerRoutePinsV1(
        "1" * 64,
        "2" * 64,
        sha(profile[11].encode("ascii")),
        owner.host,
        owner.port,
        owner.database,
        owner.username,
        profile[5],
        profile[4],
        "0056",
    )
    role_witness = await observe_restricted_native_roles_0056_witness_v1(
        selected, source=control.deployment._SOURCE
    )
    scoped_control = DemoControlRepository(
        owner_factory, clock=lambda: datetime.now(UTC) - timedelta(seconds=1)
    )
    scope = ControlScope("demo", "730032")
    stop = await scoped_control.latch_stop(scope, command_id="a" * 64)
    arguments = {
        "event_sha256": stop.event_sha256,
        "pins": pins,
        "server_incarnation_sha256": postgrant._owner_server_sha256(actor),
    }
    seed = genesis.seed_owner_legacy_control_genesis_0056_v1
    async with selected.writer_factory() as restricted, restricted.begin():
        with pytest.raises(DBAPIError) as denied:
            await restricted.execute(
                text("INSERT INTO public.demo_automation_state(id) VALUES (1)")
            )
        assert denied.value.orig.sqlstate == "42501"
    with pytest.raises(genesis.LegacyControlGenesis0056Error):
        await seed(owner_factory, **arguments, reconcile_only=True)
    with pytest.raises(genesis.LegacyControlGenesis0056Error):
        await seed(
            owner_factory,
            **{**arguments, "event_sha256": "b" * 64},
            reconcile_only=False,
        )
    async with owner_engine.begin() as competing:
        await competing.execute(
            text("LOCK TABLE public.demo_automation_runs IN ROW EXCLUSIVE MODE")
        )
        with pytest.raises(genesis.LegacyControlGenesis0056Error):
            await seed(owner_factory, **arguments, reconcile_only=False)
    async with owner_factory() as session, session.begin():
        session.add(
            DemoAutomationFingerprint(
                fingerprint="c" * 64,
                expires_at=datetime.now(UTC) + timedelta(days=1),
                details={},
            )
        )
    with pytest.raises(genesis.LegacyControlGenesis0056Error):
        await seed(owner_factory, **arguments, reconcile_only=False)
    async with owner_factory() as session, session.begin():
        assert await session.get(DemoAutomationState, 1) is None
        saved = await session.get(DemoAutomationFingerprint, "c" * 64)
        assert saved is not None
        await session.delete(saved)
    created = await seed(owner_factory, **arguments, reconcile_only=False)
    assert created.status == "STOPPED_CREATED"
    assert created.emergency_stop is created.locked is True
    assert (
        created.armed
        is created.startup_admission
        is created.execution_authority
        is False
    )
    assert created.updated_at > stop.state.updated_at
    assert created.control_revision == 1
    replay = await seed(owner_factory, **arguments, reconcile_only=True)
    assert replay.status == "STOPPED_OBSERVED"
    assert replay.updated_at == created.updated_at
    await genesis.read_restricted_legacy_control_genesis_0056_v1(
        selected,
        role_witness,
        event_sha256=stop.event_sha256,
        expected_at=created.updated_at,
    )
    async with owner_factory() as session, session.begin():
        saved = await session.get(DemoAutomationState, 1)
        assert saved.restart_latch_required is True
        assert saved.baseline_equity is saved.peak_equity is None
        assert (
            saved.lock_reasons[-1]
            == f"native_legacy_genesis_control_{stop.event_sha256}"
        )
        await session.execute(update(DemoAutomationState).values(control_revision=2))
    with pytest.raises(genesis.LegacyControlGenesis0056Error):
        await seed(owner_factory, **arguments, reconcile_only=False)


async def test_actual0056_restricted_control_repository_reads_back_lifecycle(
    _actual_0056_role_observation_lab,  # noqa: F811
    monkeypatch,
):
    from app.core.native_role_witness_0056_v1 import (
        observe_restricted_native_roles_0056_witness_v1,
    )
    from app.database.repositories import demo_control_current56_v1 as repository56
    from app.database.repositories import (
        demo_operator_acknowledgements_current55_v1 as operator,
    )
    from app.database.repositories.demo_control import DemoControlRepository
    from app.database.repositories.qualification_ledger import (
        QualificationLedgerRepository,
    )
    from app.database.repositories.qualification_native_checkpoint_v2 import (
        held_qualification_checkpoint_0056_v1,
    )
    from app.trade_qualification.demo_control import (
        ControlPins,
        ControlScope,
        DemoControlError,
        account_lock_key,
    )
    from app.trade_qualification.demo_control_runtime import DemoControlOwner
    from app.trade_qualification.reservations import (
        LedgerScope,
        QualificationLedgerError,
    )

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
        selected,
        roles=roles,
        scope=scope,
        clock=clock,
        expected_acquire_event=genesis.event_sha256,
    )
    assert (await current.verify_native_deployment_v2()).state == genesis.state
    stale = repository56.DemoControlRepository0056V1(
        selected,
        roles=roles,
        scope=scope,
        clock=clock,
        expected_acquire_event="7" * 64,
    )
    stale_pins = ControlPins("b" * 64, "c" * 64, "d" * 64)
    with pytest.raises(repository56.DemoControlRepository0056Error):
        await stale.acquire(
            scope,
            owner_token=b"g" * 32,
            pins=stale_pins,
            command_id="6" * 64,
        )
    with pytest.raises(DemoControlError, match="control_owner_uncertain"):
        await DemoControlOwner(stale, scope, stale_pins).start()
    assert (await current.read(scope)).event_sha256 == genesis.event_sha256
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
    ledger_scope = LedgerScope(account_id=scope.account_id, settlement_currency="USDT")
    await QualificationLedgerRepository(
        owner_factory, clock=clock
    ).initialize_capture_scope(ledger_scope)
    ledger = QualificationLedgerRepository(selected.writer_factory, clock=clock)
    async with selected.writer_factory() as session, session.begin():
        async with held_qualification_checkpoint_0056_v1(
            selected, session, scope, roles=roles
        ) as held:
            local_scope = await ledger._locked(session, ledger_scope)
            assert await held.recheck_control() == (
                reopened.state,
                reopened.event_sha256,
            )
            checkpoint = await ledger._bootstrap_checkpoint(
                session, ledger_scope, local_scope, _held0056=held
            )
            assert (
                checkpoint.state.account_revision
                == checkpoint.state.ledger_revision
                == 0
            )
            async with owner_engine.begin() as competing:
                with pytest.raises(DBAPIError) as blocked:
                    await competing.exec_driver_sql(
                        "LOCK TABLE public.demo_collector_issued_account_revisions IN ACCESS EXCLUSIVE MODE NOWAIT"
                    )
                assert blocked.value.orig.sqlstate == "55P03"
        with pytest.raises(
            QualificationLedgerError,
            match="ledger56_checkpoint_original_scope_required",
        ):
            await ledger._bootstrap_checkpoint(
                session, ledger_scope, local_scope, _held0056=held
            )

    # Verify the operator's actual restricted TLS lock path, without minting
    # an operator capability or making a trading/clear claim from fixture data.
    replay = SimpleNamespace(
        _owned_database_0056=(selected, roles),
        session_factory=selected.writer_factory,
        previous_control=reopened.state,
    )
    async with (
        selected.writer_factory() as session,
        session.begin(),
        operator._transaction(session, lambda: replay) as hold,
    ):
        assert await hold.recheck_control() == (reopened.state, reopened.event_sha256)
        async with owner_engine.begin() as competing:
            with pytest.raises(DBAPIError) as blocked:
                await competing.exec_driver_sql(
                    "LOCK TABLE public.demo_control_clear_acknowledgements_v2 "
                    "IN ACCESS EXCLUSIVE MODE NOWAIT"
                )
            assert blocked.value.orig.sqlstate == "55P03"
    assert (await current.read(scope)).event_sha256 == reopened.event_sha256


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
