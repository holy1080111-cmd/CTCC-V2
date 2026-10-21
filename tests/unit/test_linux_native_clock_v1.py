"""Synthetic kernel/runtime seams; these do not prove a real Linux profile."""

from __future__ import annotations

import base64
import copy
import pickle
from copy import deepcopy
from types import MappingProxyType, SimpleNamespace

import pytest

from app.domain import linux_native_clock_v1 as clock
from app.domain.source_primitives import canonical, sha

BASE = 1_800_000_000_000_000_000
MONO = 100_000_000_000


def test_approved_profile_pins_keep_measured_roles_and_header_compile_limits():
    expected = {
        "ctcc.linux.x86_64.glibc239.py3123.probe21bcabc.v1": "4aeaa9ae43ec9f969c30786210d09b0315f4ce8f2d5fe0a6b958a81261c4298c",
        "ctcc.linux.x86_64.glibc241.py31214.base2f17fc0.v1": "4fe499516474d4a53bcab2462e68d5dbb3a1c989d00ccecf3b5e4df516d32e35",
    }
    assert {
        name: sha(canonical(profile)) for name, profile in clock._PROFILES.items()
    } == expected
    for profile in clock._PROFILES.values():
        provenance = profile["provenance"]
        assert provenance["probe_run_id"] == 39657125200
        assert (
            provenance["probe_artifact_sha256"]
            == "72c0f6d19f18a29361c719dbda8877a4d6dc48e1cf8f0b4b6749ae4a4dea6a83"
        )
        assert provenance["compiled_header_glibc"] == "2.39"
        assert provenance["container_glibc241_headers_compiled"] is False
        assert (
            provenance["primary_headers"]["2.39"]["lp64_branch_sha256"]
            == provenance["primary_headers"]["2.41"]["lp64_branch_sha256"]
            == "648ab2266b5657ccfd486ac25bd14cff87d3648965558bd9ce2ce1a737f385d0"
        )
        assert provenance["utc_or_upstream_freshness_authority"] is False
        assert provenance["source_or_execution_authority"] is False
        assert set(profile["runtime_roles"].values()) == {
            item["path"] for item in profile["runtime_files"]
        }
        if provenance["measurement_scope"] == "host":
            assert (
                len(profile["runtime_files"]) == 5
                and "libpython" not in profile["runtime_roles"]
            )
        else:
            assert (
                len(profile["runtime_files"]) == 6
                and "libpython" in profile["runtime_roles"]
            )


@pytest.fixture
def native_seam(monkeypatch):
    """Private synthetic profile/reader substitution, never a production input."""
    files = [{"path": "/synthetic/libc.so.6", "sha256": "a" * 64, "size": 10}]
    profile = {
        "profile_id": "synthetic-test-only",
        "runtime_files": files,
        "runtime_roles": {"libc": "/synthetic/libc.so.6"},
        "python_executable": "/synthetic/python3.12",
        "libc_path": "/synthetic/libc.so.6",
        "native_symbols": {},
        "localtime_path": "/synthetic/UTC",
        "localtime_sha256": "b" * 64,
        "abi": deepcopy(clock._ABI),
        "provenance": {"kind": "synthetic_test_only"},
    }
    offsets = "monotonic           0         0\nboottime            0         0\n"
    host = {
        "runtime_files": [{**files[0], "device": 1, "inode": 2}],
        "boot_id_sha256": "c" * 64,
        "time_namespace": "time:[4026531834]",
        "namespace_offsets_ascii": offsets,
        "namespace_offsets_sha256": sha(offsets.encode("ascii")),
        "timezone_id": "Etc/UTC",
        "utc_offset_minutes": 0,
        "kernel_release": "synthetic-kernel",
        "machine": "x86_64",
    }

    class Reader:
        def __init__(self):
            self.profile = deepcopy(profile)
            self.before = deepcopy(host)
            self.index = 0
            self.fail_guard = False

        def sample(self):
            step = self.index * 200_000
            self.index += 1
            return {
                "utc_ns": BASE + step,
                "monotonic_ns": MONO + step,
                "monotonic_before_ns": MONO + step - 10_000,
                "monotonic_after_ns": MONO + step + 10_000,
                "boottime_ns": MONO + step + 20_000_000,
            }

        def status(self):
            value = clock._Timex()
            value.status = 0x0001
            value.maxerror = 10
            value.esterror = 5
            value.precision = 1
            value.tolerance = 32_768_000
            value.time.tv_sec = BASE // 1_000_000_000
            value.time.tv_usec = 100
            return 0, 0, bytes(value)

        def clock(self, _clock_id, *, resolution=False):
            assert resolution is True
            return 1

        def guard(self):
            if self.fail_guard:
                raise clock.LinuxClockError("linux_clock_runtime_changed")

    monkeypatch.setattr(
        clock, "_PROFILES", MappingProxyType({profile["profile_id"]: profile})
    )
    monkeypatch.setattr(clock, "_NativeReader", Reader)
    return Reader


def _observation(native_seam):
    return clock.replay_linux_clock_observation_v1(clock._observe(native_seam()))


def _change_status(data, **changes):
    raw = base64.b64decode(data["status"]["raw_base64"])
    value = clock._Timex.from_buffer_copy(raw)
    for key, item in changes.items():
        setattr(value, key, item)
    raw = bytes(value)
    data["status"]["raw_base64"] = base64.b64encode(raw).decode("ascii")
    data["status"]["raw_sha256"] = sha(raw)


def test_positive_owned_observation_single_use_with_unknown_upstream(native_seam):
    token = clock.observe_linux_clock_v1()
    payload = clock.consume_owned_linux_clock_v1(token)
    evidence = clock.replay_linux_clock_observation_v1(payload)
    assert evidence["status"]["return_state"] == 0
    assert evidence["uncertainty_bound_ns"] < 5_000_000
    assert evidence["upstream_server"] is None
    assert evidence["last_upstream_sync_at"] is None
    assert evidence["utc_certified"] is False
    assert evidence["execution_authority"] is False
    with pytest.raises(clock.LinuxClockError, match="owned_observation_required"):
        clock.consume_owned_linux_clock_v1(token)


def test_matching_replay_bytes_cannot_be_consumed_as_native(native_seam):
    data = _observation(native_seam)
    with pytest.raises(clock.LinuxClockError, match="owned_observation_required"):
        clock.consume_owned_linux_clock_v1(data)
    with pytest.raises(clock.LinuxClockError, match="owned_observation_required"):
        clock.OwnedLinuxClockObservationV1(object())


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_owned_token_cannot_be_copied_or_serialized(native_seam, operation):
    token = clock.observe_linux_clock_v1()
    with pytest.raises(clock.LinuxClockError, match="not_transferable"):
        operation(token)


@pytest.mark.parametrize(
    "status", [0x40, 0x1001, 0x11, 0x21, 0x81, 0x401, 0x8001, 0x10001]
)
def test_kernel_status_unknown_unsync_error_leap_hold_and_pps_deny(native_seam, status):
    data = _observation(native_seam)
    _change_status(data, status=status)
    with pytest.raises(clock.LinuxClockError, match="kernel_unsynchronized"):
        clock.replay_linux_clock_observation_v1(canonical(data))


@pytest.mark.parametrize("state", [1, 2, 3, 4, 5])
def test_only_time_ok_return_state_admitted(native_seam, state):
    data = _observation(native_seam)
    data["status"]["return_state"] = state
    with pytest.raises(clock.LinuxClockError, match="kernel_unsynchronized"):
        clock.replay_linux_clock_observation_v1(canonical(data))


def test_seccomp_denied_query_retains_errno_without_claiming_unsync(native_seam):
    data = _observation(native_seam)
    data["status"]["return_state"] = -1
    data["status"]["errno"] = 1
    with pytest.raises(
        clock.LinuxKernelStatusError, match="native_api_failed"
    ) as caught:
        clock.replay_linux_clock_observation_v1(canonical(data))
    assert caught.value.observation_bytes == canonical(data["status"])


def test_nanosecond_timex_representation_uses_documented_units(native_seam):
    data = _observation(native_seam)
    raw = base64.b64decode(data["status"]["raw_base64"])
    value = clock._Timex.from_buffer_copy(raw)
    value.status = 0x2001
    value.time.tv_usec = 100_000
    raw = bytes(value)
    data["status"]["raw_base64"] = base64.b64encode(raw).decode("ascii")
    data["status"]["raw_sha256"] = sha(raw)
    assert clock.replay_linux_clock_observation_v1(canonical(data)) == data


@pytest.mark.parametrize("status", [0, 1, 0x2000, 0x2001])
def test_pll_control_bit_is_recorded_without_inventing_upstream_sync(
    native_seam, status
):
    data = _observation(native_seam)
    value = clock._Timex.from_buffer_copy(
        base64.b64decode(data["status"]["raw_base64"])
    )
    value.status = status
    if status & 0x2000:
        value.time.tv_usec = 100_000
    raw = bytes(value)
    data["status"]["raw_base64"] = base64.b64encode(raw).decode("ascii")
    data["status"]["raw_sha256"] = sha(raw)
    data["pll_updates_enabled"] = bool(status & 1)
    replayed = clock.replay_linux_clock_observation_v1(canonical(data))
    assert replayed["pll_updates_enabled"] is bool(status & 1)
    assert replayed["upstream_server"] is None
    assert replayed["last_upstream_sync_at"] is None
    assert replayed["utc_certified"] is False
    data["pll_updates_enabled"] = not data["pll_updates_enabled"]
    with pytest.raises(clock.LinuxClockError, match="pll_mode_mismatch"):
        clock.replay_linux_clock_observation_v1(canonical(data))


@pytest.mark.parametrize(
    "changes",
    [
        {"modes": 1},
        {"maxerror": 5001},
        {"maxerror": -1},
        {"esterror": 11},
        {"precision": -1},
        {"tolerance": 0},
        {"offset": 10000},
    ],
)
def test_nonquery_input_and_unbounded_uncertainty_deny(native_seam, changes):
    data = _observation(native_seam)
    _change_status(data, **changes)
    with pytest.raises(clock.LinuxClockError):
        clock.replay_linux_clock_observation_v1(canonical(data))


def test_status_timestamp_cannot_be_relabelled_to_local_now(native_seam):
    data = _observation(native_seam)
    raw = base64.b64decode(data["status"]["raw_base64"])
    value = clock._Timex.from_buffer_copy(raw)
    value.time.tv_sec += 1
    raw = bytes(value)
    data["status"]["raw_base64"] = base64.b64encode(raw).decode("ascii")
    data["status"]["raw_sha256"] = sha(raw)
    with pytest.raises(clock.LinuxClockError, match="status_causal_order"):
        clock.replay_linux_clock_observation_v1(canonical(data))


@pytest.mark.parametrize(
    "field,value",
    [
        ("utc_certified", True),
        ("execution_authority", True),
        ("last_upstream_sync_at", "2026-01-01T00:00:00Z"),
        ("upstream_server", "invented"),
    ],
)
def test_upstream_or_authority_assertions_rejected(native_seam, field, value):
    data = _observation(native_seam)
    data[field] = value
    with pytest.raises(clock.LinuxClockError, match="authority_forbidden"):
        clock.replay_linux_clock_observation_v1(canonical(data))


@pytest.mark.parametrize(
    "mutation",
    [
        "wall_jump",
        "suspend",
        "overlap",
        "long_bracket",
        "reversed",
        "changed_boot",
        "changed_runtime",
        "namespace_offset",
        "profile",
        "unknown_field",
        "uncertainty",
        "raw_hash",
        "clock_resolution",
    ],
)
def test_changed_native_evidence_fails_closed(native_seam, mutation):
    data = _observation(native_seam)
    if mutation == "wall_jump":
        data["samples"][2]["utc_ns"] += 5_000_001
    elif mutation == "suspend":
        data["samples"][2]["boottime_ns"] += 5_000_001
    elif mutation == "overlap":
        data["samples"][1] = deepcopy(data["samples"][0])
    elif mutation == "long_bracket":
        data["samples"][1]["monotonic_after_ns"] += 1_000_001
    elif mutation == "reversed":
        data["samples"].reverse()
    elif mutation == "changed_boot":
        data["host_after"]["boot_id_sha256"] = "d" * 64
    elif mutation == "changed_runtime":
        data["host_after"]["runtime_files"][0]["inode"] += 1
    elif mutation == "namespace_offset":
        data["host_before"]["namespace_offsets_ascii"] = "monotonic 1 0\nboottime 0 0\n"
    elif mutation == "profile":
        data["profile_id"] = "unmeasured"
    elif mutation == "unknown_field":
        data["passed"] = True
    elif mutation == "uncertainty":
        data["uncertainty_bound_ns"] = 0
    elif mutation == "raw_hash":
        data["status"]["raw_sha256"] = "0" * 64
    elif mutation == "clock_resolution":
        data["resolutions_ns"]["realtime"] = 1001
    with pytest.raises((clock.LinuxClockError, ValueError)):
        clock.replay_linux_clock_observation_v1(canonical(data))


def test_observation_native_guard_failure_denies_token(native_seam, monkeypatch):
    class Broken(native_seam):
        def guard(self):
            raise clock.LinuxClockError("linux_clock_runtime_changed")

    monkeypatch.setattr(clock, "_NativeReader", Broken)
    with pytest.raises(clock.LinuxClockError, match="runtime_changed"):
        clock.observe_linux_clock_v1()


def test_expired_token_is_consumed_without_reissue(native_seam, monkeypatch):
    class Expired(native_seam):
        def sample(self):
            value = super().sample()
            if self.index > 3:
                for key in value:
                    value[key] += 6_000_000_000
            return value

    monkeypatch.setattr(clock, "_NativeReader", Expired)
    token = clock.observe_linux_clock_v1()
    with pytest.raises(clock.LinuxClockError, match="probe_expired"):
        clock.consume_owned_linux_clock_v1(token)
    with pytest.raises(clock.LinuxClockError, match="owned_observation_required"):
        clock.consume_owned_linux_clock_v1(token)


@pytest.mark.parametrize("variant", ["overlapping_bracket", "boottime_reversed"])
def test_consume_fourth_sample_enforces_complete_pairwise_order(
    native_seam, monkeypatch, variant
):
    class Changed(native_seam):
        def sample(self):
            value = super().sample()
            if self.index == 4:
                if variant == "overlapping_bracket":
                    value["monotonic_before_ns"] = MONO + 410_000 - 1
                    value["monotonic_ns"] = (
                        value["monotonic_before_ns"] + value["monotonic_after_ns"]
                    ) // 2
                else:
                    # One ns reverse is well below the 5 ms suspend tolerance.
                    value["boottime_ns"] = MONO + 400_000 + 20_000_000 - 1
            return value

    monkeypatch.setattr(clock, "_NativeReader", Changed)
    token = clock.observe_linux_clock_v1()
    with pytest.raises(clock.LinuxClockError, match="suspend_or_reversed"):
        clock.consume_owned_linux_clock_v1(token)
    with pytest.raises(clock.LinuxClockError, match="owned_observation_required"):
        clock.consume_owned_linux_clock_v1(token)


def test_replay_rejects_noncanonical_bytes(native_seam):
    data = _observation(native_seam)
    with pytest.raises(clock.LinuxClockError, match="noncanonical"):
        clock.replay_linux_clock_observation_v1(canonical(data) + b"\n")


def test_existing_windows_validator_does_not_silently_admit_linux(native_seam):
    from app.domain.native_clock import validate_os_clock

    data = _observation(native_seam)
    with pytest.raises(ValueError, match="os_clock_evidence_invalid"):
        validate_os_clock(data)


@pytest.fixture
def runtime_os_seam(monkeypatch):
    """Exercise actual runtime_snapshot code with explicit synthetic OS reads."""
    roles = {
        "executable": "/runtime/python3.12",
        "libc": "/runtime/libc.so.6",
        "loader": "/runtime/ld-linux-x86-64.so.2",
        "ctypes": "/runtime/_ctypes.cpython-312-x86_64-linux-gnu.so",
        "libffi": "/runtime/libffi.so.8.1.4",
    }
    state = SimpleNamespace(
        roles=roles,
        files={},
        snapshots={},
        mapped=[],
        exe=roles["executable"],
        ctypes=roles["ctypes"],
        namespace="time:[4026531834]",
        offsets=b"monotonic 0 0\nboottime 0 0\n",
        timezone=b"synthetic-UTC",
        boot=b"12345678-1234-1234-1234-123456789abc\n",
        guard_calls=0,
    )

    def build(shared=False):
        selected = {
            **roles,
            **({"libpython": "/runtime/libpython3.12.so.1.0"} if shared else {}),
        }
        files = []
        state.mapped.clear()
        for index, (role, path) in enumerate(sorted(selected.items())):
            raw = b"synthetic native runtime role:" + role.encode("ascii")
            state.files[path] = raw
            state.snapshots[path] = SimpleNamespace(st_dev=1, st_ino=index + 100)
            files.append({"path": path, "sha256": sha(raw), "size": len(raw)})
            state.mapped.append(f"1000-2000 r-xp 00000000 00:01 {index + 100} {path}\n")
        return {
            "profile_id": "synthetic-runtime-os-only",
            "runtime_files": files,
            "runtime_roles": selected,
            "python_executable": roles["executable"],
            "libc_path": roles["libc"],
            "native_symbols": {
                name: {}
                for name in ("clock_gettime", "clock_getres", "adjtimex", "dladdr")
            },
            "localtime_path": "/runtime/UTC",
            "localtime_sha256": sha(state.timezone),
            "abi": deepcopy(clock._ABI),
            "provenance": {"kind": "synthetic_test_only"},
        }

    class NativePath:
        def __init__(self, value):
            self.value = str(value)

        def as_posix(self):
            return self.value

        def resolve(self, *, strict):
            assert strict is True
            if self.value == "/proc/123/exe":
                return NativePath(state.exe)
            if self.value == "/runtime/loaded-ctypes":
                return NativePath(state.ctypes)
            if self.value == "/etc/localtime":
                return NativePath("/runtime/UTC")
            return self

        def __str__(self):
            return self.value

    def read(path, limit, *, regular=False):
        if path in state.files:
            assert regular is True
            return state.files[path], state.snapshots[path]
        raw = {
            "/proc/123/maps": "".join(state.mapped).encode("ascii"),
            "/runtime/UTC": state.timezone,
            "/proc/sys/kernel/random/boot_id": state.boot,
            "/proc/123/timens_offsets": state.offsets,
        }[path]
        assert len(raw) <= limit
        return raw, SimpleNamespace()

    def guard():
        state.guard_calls += 1

    monkeypatch.setattr(clock, "_environment_guard", guard)
    monkeypatch.setattr(clock, "_read_fixed", read)
    monkeypatch.setattr(clock, "Path", NativePath)
    monkeypatch.setattr(
        clock,
        "os",
        SimpleNamespace(
            getpid=lambda: 123,
            makedev=lambda major, minor: major * 256 + minor,
            readlink=lambda _path: state.namespace,
            uname=lambda: SimpleNamespace(machine="x86_64", release="synthetic-kernel"),
        ),
    )
    monkeypatch.setattr(clock, "sys", SimpleNamespace(executable=roles["executable"]))
    monkeypatch.setattr(
        clock, "_ctypes", SimpleNamespace(__file__="/runtime/loaded-ctypes")
    )
    state.build = build
    return state


@pytest.mark.parametrize("shared", [False, True])
def test_runtime_snapshot_accepts_exact_required_roles_with_optional_shared_python(
    runtime_os_seam, shared
):
    state = runtime_os_seam
    profile = state.build(shared=shared)
    observed = clock._runtime_snapshot(profile)
    assert len(observed["runtime_files"]) == (6 if shared else 5)
    assert state.guard_calls == 1
    assert {item["path"] for item in observed["runtime_files"]} == set(
        profile["runtime_roles"].values()
    )


@pytest.mark.parametrize(
    "variant",
    [
        "missing_role",
        "extra_role",
        "role_alias",
        "missing_file",
        "extra_file",
        "wrong_exe",
        "wrong_ctypes",
        "changed_bytes",
        "missing_mapping",
        "changed_inode",
        "wx_mapping",
        "namespace_offset",
        "timezone",
        "boot",
        "namespace",
    ],
)
def test_runtime_snapshot_rejects_changed_os_provenance(runtime_os_seam, variant):
    state = runtime_os_seam
    profile = state.build()
    if variant == "missing_role":
        profile["runtime_roles"].pop("loader")
    elif variant == "extra_role":
        profile["runtime_roles"]["unreviewed"] = "/runtime/extra.so"
    elif variant == "role_alias":
        profile["runtime_roles"]["libffi"] = profile["libc_path"]
    elif variant == "missing_file":
        profile["runtime_files"].pop()
    elif variant == "extra_file":
        profile["runtime_files"].append(
            {"path": "/extra.so", "sha256": "a" * 64, "size": 1}
        )
    elif variant == "wrong_exe":
        state.exe = "/runtime/different-python"
    elif variant == "wrong_ctypes":
        state.ctypes = "/runtime/different-ctypes"
    elif variant == "changed_bytes":
        state.files[profile["libc_path"]] = b"changed"
    elif variant == "missing_mapping":
        state.mapped.pop()
    elif variant == "changed_inode":
        state.snapshots[profile["libc_path"]].st_ino += 1
    elif variant == "wx_mapping":
        state.mapped[0] = state.mapped[0].replace("r-xp", "rwxp")
    elif variant == "namespace_offset":
        state.offsets = b"monotonic 1 0\nboottime 0 0\n"
    elif variant == "timezone":
        state.timezone = b"changed zone"
    elif variant == "boot":
        state.boot = b"not a boot UUID"
    elif variant == "namespace":
        state.namespace = "unknown"
    with pytest.raises(clock.LinuxClockError):
        clock._runtime_snapshot(profile)
