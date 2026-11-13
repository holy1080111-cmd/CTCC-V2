"""Verify monitor HTTP and exact installed-source checks with restricted TLS PostgreSQL."""

import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import update

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
