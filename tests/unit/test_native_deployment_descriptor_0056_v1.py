"""DB0056 stable deployment descriptor publication and restricted replay."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.native_only_lifespan import _MUST_BE_FALSE
from app.core.native_role_identity_0056_v2 import NativeRoleIdentity0056V2
from app.domain.source_primitives import canonical, sha
from scripts import native_deployment_descriptor_0056_v1 as subject
from tests.unit.test_native_owner_grant_0056_draft_v1 import _pins


def _descriptor() -> subject._Descriptor:
    fields = dict.fromkeys(subject._Descriptor.__dataclass_fields__, "c" * 64)
    fields.update(source_commit="a" * 40, source_tree="b" * 40)
    return subject._Descriptor(**fields)


def test_one_descriptor_published_and_independently_reopened(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "_require_private_file", lambda _: None)
    monkeypatch.setattr(subject, "require_private_evidence_base_v2", Path)
    descriptor = _descriptor()
    digest = subject._publish_descriptor(tmp_path, descriptor)
    path = tmp_path / subject._LEAF
    assert sha(path.read_bytes()) == digest
    assert subject._read_private_descriptor(path, digest) == descriptor
    assert "server_incarnation" not in path.read_text("ascii")
    with pytest.raises(OSError):
        subject._publish_descriptor(tmp_path, replace(descriptor, source_tree="d" * 40))
    assert subject._read_private_descriptor(path, digest) == descriptor


@pytest.mark.parametrize(
    "defect", ["bytes", "unknown_key", "noncanonical", "duplicate"]
)
def test_private_descriptor_rejects_bad_bytes(tmp_path, monkeypatch, defect):
    monkeypatch.setattr(subject, "_require_private_file", lambda _: None)
    descriptor = _descriptor()
    raw = descriptor.payload
    if defect == "bytes":
        digest = "f" * 64
    else:
        if defect == "unknown_key":
            raw = canonical({**json.loads(raw), "startup_admission": True})
        elif defect == "noncanonical":
            raw += b"\n"
        elif defect == "duplicate":
            raw = b'{"database_head":"0056",' + raw[1:]
        digest = sha(raw)
    file = tmp_path / subject._LEAF
    file.write_bytes(raw)
    with pytest.raises(ValueError):
        subject._read_private_descriptor(file, digest)


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", [None, "cluster", "plan", "portable", "authority"])
async def test_owner_export_requires_postgrant_and_independent_portable_match(
    monkeypatch, drift
):
    pins = _pins()
    open_plan = Mock(return_value=pins)
    reference = SimpleNamespace(
        inherited_native_definitions_sha256=pins.portable_inherited_definitions_sha256,
        fitted_normalized_owner_catalog_sha256=pins.portable_fitted_pregrant_normalized_sha256,
        reference_sha256="8" * 64,
    )
    result = {
        "schema": subject._OWNER_READBACK_SCHEMA,
        "status": "OBSERVED_DENY",
        "database_head": "0056",
        "restricted_logins_observed": 3,
        "separate_read_only_owner_observations": 2,
        "target_grant_pins_sha256": sha(canonical(asdict(pins))),
        "cluster_system_identifier_sha256": pins.route.cluster_system_identifier_sha256,
        "inherited_native_definitions_sha256": pins.portable_inherited_definitions_sha256,
        "granted_inherited_acl_shape_sha256": "9" * 64,
        "restricted_role_identity_sha256": "a" * 64,
        "startup_admission": False,
        "execution_authority": False,
        "external_approval_authenticated": False,
        "admission": "DENY",
    }
    if drift == "cluster":
        result["cluster_system_identifier_sha256"] = "b" * 64
    elif drift == "plan":
        open_plan.side_effect = [pins, replace(pins, source_commit="c" * 40)]
    elif drift == "portable":
        reference.fitted_normalized_owner_catalog_sha256 = "d" * 64
    elif drift == "authority":
        result["startup_admission"] = True
    observe = AsyncMock(return_value=result)
    publish = Mock(return_value="e" * 64)
    monkeypatch.setattr(subject, "require_private_evidence_base_v2", Path)
    monkeypatch.setattr(subject, "_load_plan", open_plan)
    monkeypatch.setattr(
        subject, "load_reviewed_owner_portable_0056_v1", lambda _: reference
    )
    monkeypatch.setattr(subject, "observe_owner_postgrant_readback_0056_v1", observe)
    monkeypatch.setattr(subject, "_publish_descriptor", publish)
    args = {
        "plan_file": Path("plan"),
        "plan_file_sha256": "1" * 64,
        "owner_dsn_file": Path("owner"),
        "ca_file": Path("CA"),
        "source_archive_file": Path("source"),
        "descriptor_directory": Path("private"),
    }
    if drift:
        with pytest.raises(subject.NativeDeploymentDescriptor0056Error):
            await subject.export_owner_verified_descriptor_0056_v1(**args)
        publish.assert_not_called()
    else:
        assert (
            await subject.export_owner_verified_descriptor_0056_v1(**args) == "e" * 64
        )
        observe.assert_awaited_once()
        published = publish.call_args.args[1]
        assert published.tls_ca_sha256 == pins.route.ca_sha256
        assert (
            published.owner_observed_cluster_sha256
            == pins.route.cluster_system_identifier_sha256
        )
        assert "owner_dsn" not in published.payload.decode("ascii")


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", [None, "identity", "acl", "manifest", "descriptor"])
async def test_restricted_replay_compares_only_stable_data_and_reopens_source(
    monkeypatch, drift
):
    descriptor = _descriptor()
    actual = NativeRoleIdentity0056V2(
        database_oid=12,
        owner_oid=13,
        role_names=("writer", "custody", "frame"),
        role_oids=(21, 22, 23),
        stable_identity_sha256=descriptor.restricted_role_identity_sha256,
        server_incarnation_sha256="f" * 64,
        role_route_sha256="e" * 64,
        inherited_native_definitions_sha256=descriptor.inherited_native_definitions_sha256,
        granted_inherited_acl_shape_sha256=descriptor.granted_inherited_acl_shape_sha256,
    )
    if drift == "identity":
        actual = replace(actual, stable_identity_sha256="b" * 64)
    elif drift == "acl":
        actual = replace(actual, granted_inherited_acl_shape_sha256="b" * 64)
    match_source = Mock()
    if drift == "manifest":
        match_source.side_effect = [None, subject.NativeDeploymentDescriptor0056Error()]
    read_descriptor = Mock(return_value=descriptor)
    if drift == "descriptor":
        read_descriptor.side_effect = [
            descriptor,
            replace(descriptor, owner_grant_plan_sha256="b" * 64),
        ]
    selected = object()
    observe = AsyncMock(return_value=actual)
    dispose = AsyncMock()
    monkeypatch.setattr(subject, "_read_private_descriptor", read_descriptor)
    monkeypatch.setattr(subject, "_check_installed_release", match_source)
    monkeypatch.setattr(subject, "_check_runtime_settings", Mock())
    settings = object()
    monkeypatch.setattr(subject, "get_settings", lambda: settings)
    monkeypatch.setattr(
        subject.identity, "observe_restricted_role_identity_0056_v2", observe
    )
    monkeypatch.setattr(
        subject.selectors,
        "select_configured_native_demo_factories_v2",
        lambda: selected,
    )
    monkeypatch.setattr(
        subject.selectors, "_dispose_failed_selection_factories_v2", dispose
    )
    owner = AsyncMock()
    monkeypatch.setattr(subject, "observe_owner_postgrant_readback_0056_v1", owner)
    args = {
        "descriptor_file": Path("descriptor"),
        "descriptor_sha256": "1" * 64,
        "source_archive_file": Path("source"),
    }
    if drift:
        with pytest.raises(subject.NativeDeploymentDescriptor0056Error):
            await subject.verify_restricted_deployment_descriptor_0056_v1(**args)
    else:
        assert (
            await subject.verify_restricted_deployment_descriptor_0056_v1(**args)
            == args["descriptor_sha256"]
        )
        assert match_source.call_count == read_descriptor.call_count == 2
    owner.assert_not_called()
    dispose.assert_awaited_once_with(selected)


def test_cli_successful_match_never_authorizes_startup(monkeypatch, capsys):
    verify = AsyncMock(return_value="a" * 64)
    monkeypatch.setattr(
        subject, "verify_restricted_deployment_descriptor_0056_v1", verify
    )
    assert (
        subject.main(
            [
                "verify-restricted",
                "--descriptor-file",
                "private",
                "--descriptor-sha256",
                "a" * 64,
                "--source-archive-file",
                "source",
            ]
        )
        == 2
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "RESTRICTED_MATCH_OBSERVED"
    assert result["owner_cluster_observed_in_invocation"] is False
    assert result["startup_admission"] is result["execution_authority"] is False
    assert result["deployment_reference_authenticated"] is False
    assert result["admission"] == "DENY"


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["write_enabled", "wrong_ca", "unreadable_ca"])
async def test_bad_runtime_profile_denies_before_opening_roles(monkeypatch, defect):
    descriptor = _descriptor()
    settings = SimpleNamespace(
        ctcc_native_only_lifespan_v1=True,
        ctcc_native_pg_tls_ca_sha256=descriptor.tls_ca_sha256,
        ctcc_native_pg_tls_ca_file="/approved/ca.pem",
        environment="production",
        trading_mode="okx_demo",
        web_concurrency=1,
        okx_demo_enabled=True,
        **dict.fromkeys(_MUST_BE_FALSE, False),
    )
    ca = Mock()
    if defect == "write_enabled":
        settings.okx_demo_allow_order_writes = True
    elif defect == "wrong_ca":
        settings.ctcc_native_pg_tls_ca_sha256 = "a" * 64
    else:
        ca.side_effect = OSError
    monkeypatch.setattr(subject, "_read_private_descriptor", lambda *args: descriptor)
    monkeypatch.setattr(subject, "_check_installed_release", Mock())
    monkeypatch.setattr(subject, "get_settings", lambda: settings)
    monkeypatch.setattr(subject, "verified_native_pg_tls_context_v1", ca)
    select = Mock()
    monkeypatch.setattr(
        subject.selectors, "select_configured_native_demo_factories_v2", select
    )
    with pytest.raises((ValueError, OSError, RuntimeError)):
        await subject.verify_restricted_deployment_descriptor_0056_v1(
            descriptor_file=Path("descriptor"),
            descriptor_sha256="a" * 64,
            source_archive_file=Path("source"),
        )
    select.assert_not_called()
