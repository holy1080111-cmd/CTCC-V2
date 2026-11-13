"""The DB0056 status command reports only consistent, pinned Control."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.native_only_lifespan import ExistingControl
from app.core.native_role_identity_0056_v2 import NativeRoleIdentity0056V2
from app.core.native_startup_control_observation_0056_v1 import (
    NativeStartupControl0056ObservationV1,
    NativeStartupControl0056WitnessV1,
)
from scripts import native_control_status_0056_v1 as status
from tests.unit.test_native_deployment_descriptor_0056_v1 import _descriptor


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem",
    [None, "pin", "control", "incarnation", "route", "roles", "trust", "settings"],
)
async def test_control_status_replays_pins_and_original_stored_control(
    monkeypatch, problem
):
    descriptor = _descriptor()
    anchor = status.trust.AdminStartupTrust0056V1(
        **{name: getattr(descriptor, name) for name in status._PIN_FIELDS},
        descriptor_sha256="d" * 64,
        trust_sha256="e" * 64,
    )
    anchor_after = (
        replace(anchor, trust_sha256="f" * 64) if problem == "trust" else anchor
    )
    if problem == "pin":
        anchor = replace(anchor, tls_ca_sha256="9" * 64)
    read_anchor = Mock(side_effect=(anchor, anchor_after))
    monkeypatch.setattr(status.trust, "read_native_startup_trust_0056_v1", read_anchor)
    read_descriptor = Mock(return_value=descriptor)
    monkeypatch.setattr(status.deployment, "_read_private_descriptor", read_descriptor)
    release = Mock()
    monkeypatch.setattr(status.deployment, "_check_installed_release", release)
    settings = object()
    monkeypatch.setattr(
        status.deployment,
        "get_settings",
        Mock(side_effect=(settings, object() if problem == "settings" else settings)),
    )
    runtime = Mock()
    monkeypatch.setattr(status.deployment, "_check_runtime_settings", runtime)
    states = status.deployment.selectors
    selected = object()
    select = Mock(return_value=selected)
    monkeypatch.setattr(states, "select_configured_native_demo_factories_v2", select)
    dispose = AsyncMock()
    monkeypatch.setattr(states, "_dispose_failed_selection_factories_v2", dispose)
    roles = NativeRoleIdentity0056V2(
        12,
        13,
        ("writer", "custody", "frame"),
        (21, 22, 23),
        "9" * 64 if problem == "roles" else descriptor.restricted_role_identity_sha256,
        "1" * 64,
        "2" * 64,
        descriptor.inherited_native_definitions_sha256,
        descriptor.granted_inherited_acl_shape_sha256,
    )
    observe_roles = AsyncMock(return_value=roles)
    monkeypatch.setattr(
        status.deployment.identity,
        "observe_restricted_role_identity_0056_v2",
        observe_roles,
    )
    initial = NativeStartupControl0056WitnessV1(
        NativeStartupControl0056ObservationV1(
            ExistingControl(88, False, True, True, True)
        ),
        "3" * 64,
        roles.server_incarnation_sha256,
        roles.role_route_sha256,
    )
    changed = {
        "control": {"control_snapshot_sha256": "4" * 64},
        "incarnation": {"server_incarnation_sha256": "4" * 64},
        "route": {"role_route_sha256": "4" * 64},
    }
    observe_control = AsyncMock(
        side_effect=(initial, replace(initial, **changed.get(problem, {})))
    )
    monkeypatch.setattr(
        status.control, "observe_startup_control_0056_witness_v1", observe_control
    )
    options = {
        "descriptor_file": Path("descriptor"),
        "source_archive_file": Path("source"),
    }
    if problem is None:
        result = await status.read_restricted_control_status_0056_v1(**options)
        assert result["control_revision"] == 88
        assert result["stored_armed"] is False
        assert result["emergency_stop"] is result["restart_latch_required"] is True
        assert result["source_commit"] == anchor.source_commit
        assert result["control_snapshot_sha256"] == initial.control_snapshot_sha256
        assert observe_control.await_count == 2
        assert (
            runtime.call_count == read_descriptor.call_count == release.call_count == 2
        )
    else:
        with pytest.raises(status.deployment.NativeDeploymentDescriptor0056Error):
            await status.read_restricted_control_status_0056_v1(**options)
    if problem == "pin":
        select.assert_not_called()
        dispose.assert_not_awaited()
    else:
        dispose.assert_awaited_once_with(selected)


def test_private_cli_has_no_anchor_or_authority_argument(monkeypatch, capsys):
    inspect = AsyncMock(side_effect=RuntimeError("private-connection-detail"))
    monkeypatch.setattr(status, "read_restricted_control_status_0056_v1", inspect)
    args = ["--descriptor-file", "descriptor", "--source-archive-file", "exact.tar"]
    assert status.main([*args, "--trust-file", "operator-input"]) == 1
    inspect.assert_not_awaited()
    assert status.main(args) == 1
    inspect.assert_awaited_once()
    packet = capsys.readouterr().out
    assert "private-connection-detail" not in packet
    assert "operator-input" not in packet
    assert packet.count('"admission":"DENY"') == 2
    assert packet.count('"order_writes":0') == 2
