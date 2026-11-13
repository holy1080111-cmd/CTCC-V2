"""Offline DB0056 startup candidate tests; no PostgreSQL or provider I/O."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.core import native_role_preflight_0056_full_diagnostic_v1 as roles56
from app.core import native_startup_candidate_0056_v1 as subject
from app.core import native_startup_control_observation_0056_v1 as control56
from app.core.native_only_lifespan import ExistingControl
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)
from app.domain.source_primitives import sha
from scripts import manifest


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    (tmp_path / "source.py").write_text("reviewed = True\n", encoding="utf-8")
    manifest.write_manifest(tmp_path, tmp_path / "MANIFEST.sha256")
    source_sha = sha((tmp_path / "MANIFEST.sha256").read_bytes())
    role = roles56.RestrictedNativeRoles0056WitnessV1(
        roles56.RestrictedNativeRoles0056ObservationV1(
            "1" * 64, "2" * 64, "3" * 64, "4" * 64
        ),
        "5" * 64,
        "6" * 64,
    )
    control = control56.NativeStartupControl0056WitnessV1(
        control56.NativeStartupControl0056ObservationV1(
            ExistingControl(7, True, False, False, True)
        ),
        "7" * 64,
        "5" * 64,
        "6" * 64,
    )
    pins = subject.NativeStartupCandidate0056PinsV1(
        source_sha, "1" * 64, "4" * 64, "2" * 64, "3" * 64, "5" * 64, "6" * 64
    )
    state = SimpleNamespace(
        source=tmp_path,
        selected=ConfiguredNativeDemoFactoriesV2(
            object(), object(), object(), object(), object()
        ),
        roles=[role, role],
        controls=[control, control],
        pins=pins,
        events=[],
        late_source_change=False,
    )

    async def role_observation(selected, *, source):
        assert selected is state.selected and source == state.source
        state.events.append("role")
        if state.late_source_change and state.events.count("role") == 2:
            (state.source / "source.py").write_text(
                "reviewed = False\n", encoding="utf-8"
            )
        return state.roles.pop(0)

    async def control_observation(selected):
        assert selected is state.selected
        state.events.append("control")
        return state.controls.pop(0)

    monkeypatch.setattr(
        roles56, "observe_restricted_native_roles_0056_witness_v1", role_observation
    )
    monkeypatch.setattr(
        control56, "observe_startup_control_0056_witness_v1", control_observation
    )
    return state


@pytest.mark.asyncio
async def test_candidate_replays_original_sequence_and_stays_deny(scenario):
    result = await subject.observe_selected_native_startup_candidate_0056_v1(
        scenario.selected, source=scenario.source, pins=scenario.pins
    )
    assert scenario.events == ["role", "control", "role", "control"]
    assert result.source_manifest_sha256 == scenario.pins.source_manifest_sha256
    assert result.control_revision == 7
    assert result.admission == "DENY"
    assert result.independent_reference_authenticated is False
    assert result.startup_admission is result.execution_authority is False
    assert "control_revision" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "role_server_restart",
        "control_server_switch",
        "role_route_change",
        "control_row_change",
        "portable_definition_pin",
        "acl_pin",
        "deployment_custody_pin",
        "deployment_server_pin",
        "source_manifest_pin",
        "source_file_change",
        "source_late_change",
        "invalid_pin",
    ],
)
async def test_candidate_refuses_changed_source_route_server_or_control(
    scenario, fault
):
    if fault == "role_server_restart":
        scenario.roles[1] = replace(
            scenario.roles[1], server_incarnation_sha256="8" * 64
        )
    elif fault == "control_server_switch":
        scenario.controls[0] = replace(
            scenario.controls[0], server_incarnation_sha256="8" * 64
        )
    elif fault == "role_route_change":
        scenario.roles[1] = replace(scenario.roles[1], role_route_sha256="8" * 64)
    elif fault == "control_row_change":
        scenario.controls[1] = replace(
            scenario.controls[1], control_snapshot_sha256="8" * 64
        )
    elif fault == "portable_definition_pin":
        scenario.pins = replace(
            scenario.pins, inherited_native_definitions_sha256="8" * 64
        )
    elif fault == "acl_pin":
        scenario.pins = replace(
            scenario.pins, granted_inherited_acl_shape_sha256="8" * 64
        )
    elif fault == "deployment_custody_pin":
        scenario.pins = replace(scenario.pins, deployment_custody_sha256="8" * 64)
    elif fault == "deployment_server_pin":
        scenario.pins = replace(
            scenario.pins, deployment_server_incarnation_sha256="8" * 64
        )
    elif fault == "source_manifest_pin":
        scenario.pins = replace(scenario.pins, source_manifest_sha256="8" * 64)
    elif fault == "source_file_change":
        (scenario.source / "source.py").write_text(
            "reviewed = False\n", encoding="utf-8"
        )
    elif fault == "source_late_change":
        scenario.late_source_change = True
    else:
        scenario.pins = replace(scenario.pins, deployment_role_route_sha256="bad")
    with pytest.raises(subject.NativeStartupCandidate0056Error):
        await subject.observe_selected_native_startup_candidate_0056_v1(
            scenario.selected, source=scenario.source, pins=scenario.pins
        )


@pytest.mark.asyncio
async def test_configured_gate_refuses_even_after_matching_diagnostic(monkeypatch):
    class Engine:
        def __init__(self):
            self.disposed = 0

        async def dispose(self):
            self.disposed += 1

    writer = object()
    custody, frame = Engine(), Engine()
    selected = ConfiguredNativeDemoFactoriesV2(
        writer, object(), object(), custody, frame
    )
    pins = subject.NativeStartupCandidate0056PinsV1(*("1" * 64 for _ in range(7)))
    monkeypatch.setattr(
        subject.selectors,
        "select_configured_native_demo_factories_v2",
        lambda: selected,
    )

    async def diagnostic(*args, **kwargs):
        return object()

    monkeypatch.setattr(
        subject, "observe_selected_native_startup_candidate_0056_v1", diagnostic
    )
    with pytest.raises(
        subject.NativeStartupCandidate0056Error,
        match="^native_startup_candidate_0056_denied$",
    ):
        await subject.preflight_configured_native_startup_candidate_0056_v1(
            writer, pins=pins
        )
    assert custody.disposed == frame.disposed == 1
