"""Synthetic actual cleanup/export helper canaries; never native acceptance."""

import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from app.domain import account_history_partition_catalog_v1 as parent
from app.domain import prospective_event_cutover_catalog_v2 as policy
from scripts import prospective_capture_custody as custody
from scripts import verify_controlled_writer_coverage_schema_v1 as coverage
from scripts import verify_final_hermetic as host

RUN_ID = "a" * 12
CONTAINER = "ctcc-final-" + RUN_ID + "-capture"
NETWORK = "ctcc-final-" + RUN_ID + "-network"
RESOURCES = [("network", NETWORK), ("container", CONTAINER)]


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class FakeDocker:
    """Only Docker transport observations replaced; real settlement runs."""

    def __init__(self, clock=None):
        self.clock = FakeClock() if clock is None else clock
        self.visible = {"container": {CONTAINER}, "network": {NETWORK}}
        self.calls = []
        self.rm_still_visible = False
        self.query_failure = None
        self.advance = {}
        self.timeout_remove = False
        self.bad_label = False
        self.after_final = None

    def __call__(self, args, **kwargs):
        if args[:2] == ["docker", "exec"]:
            # Main's readiness probe is outside the cleanup operation inventory.
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        self.calls.append((list(args), dict(kwargs)))
        ordinal = len(self.calls)
        self.clock.value += self.advance.get(ordinal, 0)
        _, kind, operation, *rest = args
        if self.query_failure == ordinal:
            return SimpleNamespace(
                returncode=1, stdout=b"private-Docker-detail", stderr=b"private-error"
            )
        if operation == "ls":
            raw = "\n".join(sorted(self.visible[kind])).encode()
            if ordinal == 8 and self.after_final is not None:
                self.after_final()
        elif operation == "inspect":
            name = rest[0]
            labels = {"org.ctcc.validation.run": "other" if self.bad_label else RUN_ID}
            row = (
                {"Name": "/" + name, "Config": {"Labels": labels}}
                if kind == "container"
                else {"Name": name, "Labels": labels}
            )
            raw = json.dumps([row]).encode()
        else:
            assert operation == "rm"
            if self.timeout_remove and kind == "container":
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
            if not (self.rm_still_visible and kind == "container"):
                self.visible[kind].remove(rest[-1])
            raw = b"remove acknowledgement only"
        return SimpleNamespace(returncode=0, stdout=raw, stderr=b"")


def _capture_bundle(monkeypatch, files=None):
    """Exact metadata contracts with sentinel-only native catalog IO seams."""
    assert policy.PG17_DEPARSED_BASELINE is None
    files = {} if files is None else dict(files)
    for name in (
        *host.PROSPECTIVE_CUTOVER_CAPTURE_PATHS,
        *host.HISTORY_PARTITION_CAPTURE_PATHS,
    ):
        files.setdefault(name, {"sha256": "a" * 64})
    files.setdefault("MANIFEST.sha256", {"sha256": "c" * 64})
    migration = custody._migration_pin(files)
    sentinel = {"explicit_synthetic_catalog_only": True}

    def new_catalog(value, *, require_native_baseline=True):
        assert value == sentinel
        assert require_native_baseline is False

    def new_deparser(value):
        assert value == sentinel
        return "f" * 64

    def parent_catalog(value, *, require_native_baseline=True):
        assert value == sentinel and require_native_baseline is True
        return parent.expected_account_history_partition_schema()

    def coverage_catalog(value, *, require_native_baseline=True):
        assert value == sentinel and require_native_baseline is True
        return host.expected_migration_revision_readback("0049")[
            "controlled_writer_coverage_schema"
        ]

    monkeypatch.setattr(policy, "verify_catalog", new_catalog)
    monkeypatch.setattr(policy, "deparser_sha256", new_deparser)
    monkeypatch.setattr(
        parent, "verify_account_history_partition_catalog", parent_catalog
    )
    monkeypatch.setattr(coverage, "verify_catalog", coverage_catalog)
    common = {
        "source_head": "0051",
        "database_head": "0051",
        "source_migration_sha256": migration,
        "catalog": sentinel,
        "session_facts": ["17.0", "170000", "ctcc", "ctcc", "ctcc", "public"],
        "native_baseline_admission": False,
        "execution_authority": False,
    }
    captures = {
        "coverage-native-catalog.json": {
            **common,
            "schema": "ctcc.controlled_writer_coverage_native_catalog_observation.v1",
        },
        "account-history-partition-native-catalog.json": {
            **common,
            "schema": "ctcc.account_history_partition_native_catalog_observation.v2",
            "source_files": {
                name: files[name]["sha256"]
                for name in host.HISTORY_PARTITION_CAPTURE_PATHS
            },
            "manifest_sha256": files["MANIFEST.sha256"]["sha256"],
        },
        "prospective-cutover-native-catalog.json": {
            **common,
            "schema": "ctcc.prospective_cutover_native_catalog_observation.v2",
            "session_facts": ["17.0", "170000", "ctcc", "ctcc", "ctcc", "pg_catalog"],
            "source_files": {
                name: files[name]["sha256"]
                for name in host.PROSPECTIVE_CUTOVER_CAPTURE_PATHS
            },
            "manifest_sha256": files["MANIFEST.sha256"]["sha256"],
            "effective_ddl_sha256": policy.ddl_identity(),
            "observed_deparser_sha256": "f" * 64,
            "g6_qualified": False,
        },
    }
    formal = {
        "schema": "ctcc.prospective_cutover_formal_catalog_probe.v2",
        "source_head": "0051",
        "database_head": "0051",
        "source_migration_sha256": migration,
        "result": "DENIED_NATIVE_BASELINE_PENDING",
        "native_baseline_admission": False,
        "g6_qualified": False,
        "execution_authority": False,
    }
    return files, captures, formal


def _inputs(tmp_path, monkeypatch):
    output = tmp_path / "run"
    output.mkdir()
    tests = output / "test-results"
    tests.mkdir()
    export = output / "export"
    export.mkdir()
    files, captures, formal = _capture_bundle(monkeypatch)
    archive = tmp_path / "source.tar"
    archive_raw = b"synthetic-exact-source-archive\n"
    archive.write_bytes(archive_raw)
    for filename, stage in custody.CAPTURES:
        raw = (
            json.dumps(
                captures[filename], sort_keys=True, separators=(",", ":")
            ).encode()
            + b"\n"
        )
        (tests / filename).write_bytes(raw)
        receipt = {
            "artifact": "test-results/" + filename,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "native_baseline_admission": False,
            "execution_authority": False,
        }
        if filename.startswith("prospective-"):
            receipt["g6_qualified"] = False
        (output / (stage + ".log")).write_text(json.dumps(receipt), encoding="utf-8")
    (output / "prospective-cutover-formal-catalog-probe.log").write_text(
        json.dumps(formal), encoding="utf-8"
    )
    identity = {
        "status": "PASS",
        "archive_sha256": hashlib.sha256(archive_raw).hexdigest(),
        "manifest_sha256": files["MANIFEST.sha256"]["sha256"],
        "current_source_migration": {
            "source_head": "0051",
            "database_head": "0051",
            "migration_sha256": custody._migration_pin(files),
        },
        "formal_catalog_probe": formal,
        "execution_authority": False,
        "external_order_writes": 0,
    }
    return {
        "output": output,
        "export": export,
        "tests": tests,
        "resources": list(RESOURCES),
        "run_id": RUN_ID,
        "files": files,
        "identity": identity,
        "steps": [],
        "archive_path": archive,
        "archive_raw": archive_raw,
    }


@pytest.mark.parametrize(
    "damage",
    (
        None,
        "rm0_still_present",
        "query_failed",
        "late_query",
        "timeout_orphan",
        "bad_label",
    ),
)
def test_actual_settlement_never_treats_rm_ack_or_unknown_as_absence(damage):
    fake = FakeDocker()
    fake.rm_still_visible = damage == "rm0_still_present"
    fake.query_failure = 7 if damage == "query_failed" else None
    fake.advance = {7: 90} if damage == "late_query" else {}
    fake.timeout_remove = damage == "timeout_orphan"
    fake.bad_label = damage == "bad_label"
    state = {"deadline": None}
    result = custody.settle_owned_capture_writers(
        list(RESOURCES), RUN_ID, state, clock=fake.clock, command=fake
    )
    assert result["verified_absent"] is (damage is None)
    assert state["deadline"] == 90
    assert all(0 < kwargs["timeout"] <= 60 for _, kwargs in fake.calls)
    if damage == "bad_label":
        assert not any(args[2] == "rm" for args, _ in fake.calls)
    assert "private-Docker-detail" not in json.dumps(result)


def test_shared_deadline_is_checked_before_commands_and_never_renewed():
    fake = FakeDocker()
    fake.clock.value = 90
    state = {"deadline": 90}
    result = custody.settle_owned_capture_writers(
        list(RESOURCES), RUN_ID, state, clock=fake.clock, command=fake
    )
    assert not result["verified_absent"] and not fake.calls and state["deadline"] == 90


def test_final_absence_uses_remaining_original_budget_and_checks_after_return():
    fake = FakeDocker()
    fake.advance = {1: 80, 2: 2, 3: 2, 4: 2, 5: 2, 6: 1, 7: 1}
    state = {"deadline": None}
    result = custody.settle_owned_capture_writers(
        list(RESOURCES), RUN_ID, state, clock=fake.clock, command=fake
    )
    assert state["deadline"] == 90 and not result["verified_absent"]
    assert [kwargs["timeout"] for _, kwargs in fake.calls] == [30, 10, 8, 6, 4, 2, 1]
    assert result["final_absence"][0]["failure_category"] == "TimeoutError"


def test_owned_name_validation_precedes_any_docker_operation():
    fake = FakeDocker()
    result = custody.settle_owned_capture_writers(
        [("container", "unrelated-user-container")],
        RUN_ID,
        {"deadline": None},
        clock=fake.clock,
        command=fake,
    )
    assert not result["verified_absent"] and fake.calls == []


@pytest.mark.parametrize("kind", ("container", "network"))
def test_untracked_owned_prefix_orphan_blocks_custody_and_is_never_deleted(kind):
    fake = FakeDocker()
    unexpected = "ctcc-final-" + RUN_ID + "-untracked-orphan"
    fake.visible[kind].add(unexpected)
    result = custody.settle_owned_capture_writers(
        list(RESOURCES), RUN_ID, {"deadline": None}, clock=fake.clock, command=fake
    )
    assert result["verified_absent"] is False
    final = next(row for row in result["final_absence"] if row["kind"] == kind)
    assert final["untracked_owned_visible"] == [unexpected]
    assert unexpected in fake.visible[kind]
    assert not any(args[2] == "rm" and args[-1] == unexpected for args, _ in fake.calls)


def test_positive_actual_helpers_settle_then_validate_export_and_keep_unsealed(
    tmp_path, monkeypatch
):
    arguments = _inputs(tmp_path, monkeypatch)
    fake = FakeDocker()
    receipt = custody.finalize_prospective_capture(
        **arguments, clock=fake.clock, command=fake
    )
    identity = arguments["identity"]
    assert identity["capture_writer_settlement"]["verified_absent"] is True
    assert identity["capture_export_accepted"] is True
    assert len(identity["trusted_capture_artifacts"]) == 3
    assert (
        identity["native_baseline_admission"] is False
        and identity["g6_qualified"] is False
    )
    assert (
        hashlib.sha256((arguments["export"] / "receipt.json").read_bytes()).hexdigest()
        == receipt
    )
    assert policy.PG17_DEPARSED_BASELINE is None


@pytest.mark.parametrize(
    "damage",
    (
        "rm0_still_present",
        "query_failed",
        "late_query",
        "timeout_orphan",
        "untracked_orphan",
    ),
)
def test_unknown_cleanup_cannot_read_export_or_inventory_any_writable_capture(
    tmp_path, monkeypatch, damage
):
    arguments = _inputs(tmp_path, monkeypatch)
    fake = FakeDocker()
    fake.rm_still_visible = damage == "rm0_still_present"
    fake.query_failure = 7 if damage == "query_failed" else None
    fake.advance = {7: 90} if damage == "late_query" else {}
    fake.timeout_remove = damage == "timeout_orphan"
    if damage == "untracked_orphan":
        fake.visible["container"].add("ctcc-final-" + RUN_ID + "-untracked-orphan")
    original_read = custody.stable_read
    seen = []

    def read(path, limit):
        seen.append(path)
        assert path.parent != arguments["tests"]
        return original_read(path, limit)

    monkeypatch.setattr(custody, "stable_read", read)
    custody.finalize_prospective_capture(**arguments, clock=fake.clock, command=fake)
    identity = arguments["identity"]
    assert (
        identity["status"] == "FAIL" and identity["trusted_capture_artifacts"] is None
    )
    assert identity["capture_export_accepted"] is False
    assert not identity["writable_capture_reads_permitted"]
    assert not (arguments["export"] / "native-catalog-capture").exists()
    assert arguments["archive_path"].read_bytes() == arguments["archive_raw"]
    receipt = json.loads((arguments["export"] / "receipt.json").read_bytes())
    assert receipt["capture_artifacts"] is None
    assert set(receipt["metadata_artifacts"]) == {
        "cleanup.json",
        "steps.json",
        "identity.json",
    }
    assert policy.PG17_DEPARSED_BASELINE is None


@pytest.mark.parametrize(
    "moment",
    ("after_absence", "after_validation_before_publish", "during_publish_before_claim"),
)
def test_post_barrier_mutation_is_not_promoted_to_capture_inventory(
    tmp_path, monkeypatch, moment
):
    arguments = _inputs(tmp_path, monkeypatch)
    path = arguments["tests"] / "prospective-cutover-native-catalog.json"
    fake = FakeDocker()

    def mutate():
        path.write_bytes(b'{"tampered_after_barrier":true}')

    if moment == "after_absence":
        fake.after_final = mutate
    else:
        original = custody._write

        def staged(path_arg, raw):
            original(path_arg, raw)
            if path_arg.parent.name == (
                "native-catalog-capture"
                if moment == "during_publish_before_claim"
                else "capture-export-staging"
            ):
                mutate()

        monkeypatch.setattr(custody, "_write", staged)
    custody.finalize_prospective_capture(**arguments, clock=fake.clock, command=fake)
    assert arguments["identity"]["capture_writer_settlement"]["verified_absent"] is True
    assert arguments["identity"]["status"] == "FAIL"
    assert arguments["identity"]["trusted_capture_artifacts"] is None
    assert (arguments["export"] / "native-catalog-capture").exists() is (
        moment == "during_publish_before_claim"
    )
    assert (
        arguments["identity"]["unaccepted_capture_export_path"]
        == "native-catalog-capture"
    )
    assert path.read_bytes() == b'{"tampered_after_barrier":true}'
    assert policy.PG17_DEPARSED_BASELINE is None


def test_original_failure_is_retained_even_when_valid_capture_is_preserved(
    tmp_path, monkeypatch
):
    arguments = _inputs(tmp_path, monkeypatch)
    arguments["identity"].update(status="FAIL", failure_type="SyntheticOriginalFailure")
    arguments["steps"].append({"name": "synthetic-failed-stage", "exit_code": 1})
    fake = FakeDocker()
    custody.finalize_prospective_capture(**arguments, clock=fake.clock, command=fake)
    identity = arguments["identity"]
    assert (
        identity["status"] == "FAIL"
        and identity["failure_type"] == "SyntheticOriginalFailure"
    )
    assert identity["capture_export_accepted"] is True
    assert (
        json.loads((arguments["export"] / "steps.json").read_bytes())
        == arguments["steps"]
    )


def _native_stat_view(*, ctime, birthtime=100, **changes):
    """Explicit synthetic path/FD observations, not a file authority receipt."""
    import stat

    value = {
        "st_dev": 1,
        "st_ino": 2,
        "st_mode": stat.S_IFREG | 0o600,
        "st_nlink": 1,
        "st_size": 3,
        "st_mtime_ns": 200,
        "st_ctime_ns": ctime,
        "st_file_attributes": 0,
    }
    if birthtime is not None:
        value["st_birthtime_ns"] = birthtime
    value.update(changes)
    return SimpleNamespace(**value)


def _stat_view_path(monkeypatch, *, platform="nt", path_views=None, fd_views=None):
    """Run actual stable_read with only native stat/byte IO observations replaced."""
    import io

    path_views = (
        [_native_stat_view(ctime=100), _native_stat_view(ctime=100)]
        if path_views is None
        else path_views
    )
    fd_views = (
        [_native_stat_view(ctime=200), _native_stat_view(ctime=200)]
        if fd_views is None
        else fd_views
    )
    observations = {"path": iter(path_views), "fd": iter(fd_views)}

    class Descriptor(io.BytesIO):
        def fileno(self):
            return 90210

    class NativePath:
        def lstat(self):
            return next(observations["path"])

        def is_symlink(self):
            return False

        def open(self, mode):
            assert mode == "rb"
            return Descriptor(b"raw")

    def fstat(descriptor):
        assert descriptor == 90210
        return next(observations["fd"])

    # Replacing this module's os reference does not mutate global os.name or
    # pathlib's platform choice and cannot turn a fixture into native evidence.
    monkeypatch.setattr(custody, "os", SimpleNamespace(name=platform, fstat=fstat))
    return NativePath()


def test_stable_read_accepts_real_unchanged_archive_after_write(tmp_path):
    path = tmp_path / "source.tar"
    path.write_bytes(b"source archive created first\n")
    raw = b"source archive after an actual write\n"
    path.write_bytes(raw)
    observed, identity = custody.stable_read(path, len(raw))
    repeated, repeated_identity = custody.stable_read(path, len(raw))
    assert observed == repeated == raw
    assert identity == repeated_identity


def test_windows_actual_reader_accepts_same_birthtime_with_cross_view_ctime_difference(
    monkeypatch,
):
    path = _stat_view_path(monkeypatch)
    raw, identity = custody.stable_read(path, 3)
    assert raw == b"raw"
    # Retain the distinct native ctime facts rather than normalizing either.
    assert identity[0][6] == 100 and identity[1][6] == 200


@pytest.mark.parametrize("view", ["path", "fd"])
@pytest.mark.parametrize("birthtime", [None, True, "100", 100.0])
def test_windows_actual_reader_refuses_missing_or_coerced_birthtime(
    monkeypatch, view, birthtime
):
    arguments = {}
    key, ctime = ("path_views", 100) if view == "path" else ("fd_views", 200)
    arguments[key] = [_native_stat_view(ctime=ctime, birthtime=birthtime)] * 2
    path = _stat_view_path(monkeypatch, **arguments)
    with pytest.raises(ValueError, match="capture_windows_birthtime_missing"):
        custody.stable_read(path, 3)


@pytest.mark.parametrize(
    "changed", ["fd_birthtime", "path_birthtime", "fd_ctime", "path_ctime"]
)
def test_windows_actual_reader_refuses_changed_birthtime_or_same_view_ctime(
    monkeypatch, changed
):
    path_views = [_native_stat_view(ctime=100), _native_stat_view(ctime=100)]
    fd_views = [_native_stat_view(ctime=200), _native_stat_view(ctime=200)]
    if changed == "fd_birthtime":
        fd_views[1] = _native_stat_view(ctime=200, birthtime=101)
    elif changed == "path_birthtime":
        path_views[1] = _native_stat_view(ctime=100, birthtime=101)
    elif changed == "fd_ctime":
        fd_views[1] = _native_stat_view(ctime=201)
    else:
        path_views[1] = _native_stat_view(ctime=101)
    path = _stat_view_path(monkeypatch, path_views=path_views, fd_views=fd_views)
    with pytest.raises(ValueError, match="capture_file_changed"):
        custody.stable_read(path, 3)


def test_windows_later_same_bytes_with_changed_fd_ctime_cannot_reuse_read_identity(
    monkeypatch,
):
    path = _stat_view_path(
        monkeypatch,
        path_views=[_native_stat_view(ctime=100)] * 4,
        fd_views=[_native_stat_view(ctime=200)] * 2
        + [_native_stat_view(ctime=201)] * 2,
    )
    first_raw, first_identity = custody.stable_read(path, 3)
    second_raw, second_identity = custody.stable_read(path, 3)
    assert first_raw == second_raw == b"raw"
    assert first_identity != second_identity


def test_posix_actual_reader_still_refuses_cross_view_ctime_difference(monkeypatch):
    path = _stat_view_path(monkeypatch, platform="posix")
    with pytest.raises(ValueError, match="capture_file_changed"):
        custody.stable_read(path, 3)
