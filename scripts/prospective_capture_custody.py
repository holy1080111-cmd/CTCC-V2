"""New0051 diagnostic custody only; no trading or native admission authority."""

import hashlib
import json
import os
import re
import stat
import subprocess
import time

CLEANUP_SECONDS = 90
CAPTURES = (
    ("prospective-cutover-native-catalog.json", "prospective-cutover-native-catalog"),
    (
        "account-history-partition-native-catalog.json",
        "history-partition-native-catalog",
    ),
    ("coverage-native-catalog.json", "coverage-native-catalog"),
)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _deadline(state, clock):
    if state["deadline"] is None:
        state["deadline"] = clock() + CLEANUP_SECONDS
    return state["deadline"]


def _owned_inventory(resources, run_id):
    if type(run_id) is not str or re.fullmatch(r"[a-f0-9]{12}", run_id) is None:
        raise ValueError("capture_cleanup_run_id_invalid")
    prefix = "ctcc-final-" + run_id + "-"
    if type(resources) is not list or len(resources) > 128:
        raise ValueError("capture_cleanup_resource_inventory_invalid")
    checked = []
    for item in resources:
        if type(item) is not tuple or len(item) != 2:
            raise ValueError("capture_cleanup_resource_inventory_invalid")
        kind, name = item
        if (
            kind not in ("container", "network")
            or type(name) is not str
            or not name.startswith(prefix)
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name[len(prefix) :]) is None
            or kind == "network"
            and name != prefix + "network"
            or item in checked
        ):
            raise ValueError("capture_cleanup_owned_name_invalid")
        checked.append(item)
    return checked


def settle_owned_capture_writers(
    resources, run_id, state, *, clock=time.monotonic, command=None
):
    """One90s budget, owned removal and independent final absence observations.

    Adapted from the reviewed original-node diagnostic settlement pattern. An
    rm rc0 is only an acknowledgement; it never proves writer absence.
    """
    command = subprocess.run if command is None else command
    record = {
        "verified_absent": False,
        "cleanup": [],
        "final_absence": [],
        "shared_budget_seconds": CLEANUP_SECONDS,
        "checks": [],
    }
    try:
        inventory = _owned_inventory(resources, run_id)
    except (TypeError, ValueError) as error:
        record["failure_category"] = type(error).__name__
        return record
    try:
        end = _deadline(state, clock)
    except Exception as error:  # noqa: BLE001 -- unknown timing cannot permit reads.
        record["failure_category"] = type(error).__name__
        return record

    def bounded(stage, args, maximum=30):
        remaining = end - clock()
        if remaining <= 0:
            raise TimeoutError("capture_shared_cleanup_deadline_exceeded")
        result = command(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=min(float(maximum), remaining),
            check=False,
        )
        if clock() >= end:
            raise TimeoutError("capture_shared_cleanup_deadline_exceeded")
        if (
            type(result.returncode) is not int
            or type(result.stdout) is not bytes
            or type(result.stderr) is not bytes
            or len(result.stdout) > 65536
            or len(result.stderr) > 65536
        ):
            raise ValueError("capture_cleanup_control_output_invalid")
        record["checks"].append(
            {
                "stage": stage,
                "exit_code": result.returncode,
                "stdout_sha256": _sha(result.stdout),
                "stderr_sha256": _sha(result.stderr),
            }
        )
        if result.returncode != 0:
            raise ValueError("capture_cleanup_control_failed")
        return result.stdout

    def listed(kind, stage):
        raw = bounded(
            stage,
            [
                "docker",
                kind,
                "ls",
                *(["-a"] if kind == "container" else []),
                "--format",
                "{{.Names}}" if kind == "container" else "{{.Name}}",
            ],
        )
        values = raw.decode("utf-8", "strict").splitlines()
        if len(values) != len(set(values)):
            raise ValueError("capture_cleanup_duplicate_listing")
        return set(values)

    for kind, name in reversed(inventory):
        item = {"kind": kind, "name": name, "status": "UNAVAILABLE"}
        try:
            if name not in listed(kind, "owned-initial-presence"):
                item["status"] = "INITIAL_ABSENCE_OBSERVED"
            else:
                details = json.loads(
                    bounded("owned-label-readback", ["docker", kind, "inspect", name])
                )
                if (
                    type(details) is not list
                    or len(details) != 1
                    or type(details[0]) is not dict
                    or details[0].get("Name")
                    != ("/" + name if kind == "container" else name)
                    or (
                        details[0].get("Config", {})
                        if kind == "container"
                        else details[0]
                    )
                    .get("Labels", {})
                    .get("org.ctcc.validation.run")
                    != run_id
                ):
                    raise ValueError("capture_cleanup_ownership_mismatch")
                bounded(
                    "owned-remove-ack",
                    [
                        "docker",
                        kind,
                        "rm",
                        *(["-f", "-v"] if kind == "container" else []),
                        name,
                    ],
                    maximum=60,
                )
                item["status"] = "REMOVE_ACKNOWLEDGED_NOT_ABSENCE"
        except Exception as error:  # noqa: BLE001 -- no Docker text is exported.
            item["failure_category"] = type(error).__name__
        record["cleanup"].append(item)
    # Separate commands use the original shared deadline. Never allocate a new
    # deadline after rm, a timeout, an exception, or a remaining-budget refusal.
    for kind in ("container", "network"):
        item = {"kind": kind, "verified_absent": False}
        try:
            visible = listed(kind, "independent-final-" + kind + "-absence")
            tracked = {name for candidate, name in inventory if candidate == kind}
            remaining = sorted(
                name
                for name in visible
                if name.startswith("ctcc-final-" + run_id + "-")
            )
            item.update(
                verified_absent=not remaining,
                owned_still_visible=remaining,
                untracked_owned_visible=sorted(set(remaining) - tracked),
            )
        except Exception as error:  # noqa: BLE001 -- unknown is never absence.
            item["failure_category"] = type(error).__name__
        record["final_absence"].append(item)
    record["verified_absent"] = (
        all(
            item["status"]
            in ("INITIAL_ABSENCE_OBSERVED", "REMOVE_ACKNOWLEDGED_NOT_ABSENCE")
            for item in record["cleanup"]
        )
        and all(item["verified_absent"] is True for item in record["final_absence"])
        and clock() < end
    )
    record["shared_deadline"] = end
    return record


def _identity(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        getattr(info, "st_file_attributes", 0),
    )


def _cross_view_identity(info):
    """Compare compatible path/FD fields without mixing Windows ctime meanings.

    Python3.12 Windows path stat can report creation time as ctime while fstat
    reports change time. Required birthtime is the shared creation field. The
    original ctime identity remains mandatory within each path or FD view.
    """
    identity = _identity(info)
    if os.name != "nt":
        return identity
    birthtime = getattr(info, "st_birthtime_ns", None)
    if type(birthtime) is not int:
        raise ValueError("capture_windows_birthtime_missing")
    return (*identity[:6], birthtime, *identity[7:])


def stable_read(path, limit):
    """Single-link ordinary file, bounded bytes, identical path/FD identity."""
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or getattr(before, "st_file_attributes", 0) & 1024
        or path.is_symlink()
        or not 0 < before.st_size <= limit
    ):
        raise ValueError("capture_file_unsafe")
    cross_view = _cross_view_identity(before)
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if _cross_view_identity(opened) != cross_view:
            raise ValueError("capture_file_changed")
        raw = stream.read(limit + 1)
        completed = os.fstat(stream.fileno())
    after = path.lstat()
    if (
        _identity(opened) != _identity(completed)
        or _identity(before) != _identity(after)
        or _cross_view_identity(completed) != cross_view
        or _cross_view_identity(after) != cross_view
        or len(raw) != before.st_size
        or not 0 < len(raw) <= limit
    ):
        raise ValueError("capture_file_changed")
    # Preserve both views for later stable rereads. On Windows a metadata-only
    # change can update FD ctime while path ctime/birthtime remain unchanged.
    return raw, (_identity(after), _identity(completed))


def _object(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("capture_duplicate_json_key")
            value[key] = item
        return value

    def finite(_value):
        raise ValueError("capture_nonfinite_json")

    value = json.loads(raw, object_pairs_hook=unique, parse_constant=finite)
    if type(value) is not dict:
        raise ValueError("capture_json_object_required")
    return value


def _write(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    retained, _ = stable_read(path, len(raw))
    if retained != raw:
        raise ValueError("capture_export_readback_changed")


def _json_write(path, value):
    raw = json.dumps(value, sort_keys=True, allow_nan=False, indent=2).encode() + b"\n"
    _write(path, raw)
    return _sha(raw)


def _migration_pin(files):
    values = {
        name: item["sha256"]
        for name, item in files.items()
        if name.startswith("migrations/") and name.endswith(".py")
    }
    if "migrations/versions/0051_prospective_event_cutover.py" not in values or any(
        type(pin) is not str or re.fullmatch(r"[a-f0-9]{64}", pin) is None
        for pin in values.values()
    ):
        raise ValueError("capture_source_migration_pins_missing")
    return _sha(json.dumps(values, sort_keys=True, separators=(",", ":")).encode())


def _validated_capture_bytes(output, tests, files, identity, archive_path, archive_raw):
    from scripts.verify_final_hermetic import (
        native_migration_json_equal,
        verify_coverage_native_catalog_capture_0051,
        verify_history_partition_native_catalog_capture_0051,
        verify_prospective_cutover_native_catalog_capture,
    )

    snapshots = {}
    retained_archive, archive_identity = stable_read(archive_path, len(archive_raw))
    if (
        retained_archive != archive_raw
        or _sha(retained_archive) != identity["archive_sha256"]
    ):
        raise ValueError("capture_source_archive_changed")
    snapshots[archive_path] = retained_archive, archive_identity
    migration = _migration_pin(files)
    expected_current = {
        "source_head": "0051",
        "database_head": "0051",
        "migration_sha256": migration,
    }
    if not native_migration_json_equal(
        identity.get("current_source_migration"), expected_current
    ):
        raise ValueError("capture_source_migration_changed")
    formal_path = output / "prospective-cutover-formal-catalog-probe.log"
    formal_raw, formal_identity = stable_read(formal_path, 65536)
    snapshots[formal_path] = formal_raw, formal_identity
    formal = _object(formal_raw)
    expected_formal = {
        "schema": "ctcc.prospective_cutover_formal_catalog_probe.v2",
        "source_head": "0051",
        "database_head": "0051",
        "source_migration_sha256": migration,
        "result": formal.get("result"),
        "native_baseline_admission": False,
        "g6_qualified": False,
        "execution_authority": False,
    }
    if (
        not native_migration_json_equal(formal, expected_formal)
        or formal.get("result")
        not in {"DENIED_NATIVE_BASELINE_PENDING", "SEALED_NATIVE_CATALOG_VERIFIED"}
        or not native_migration_json_equal(formal, identity.get("formal_catalog_probe"))
    ):
        raise ValueError("capture_formal_probe_changed")
    raw_captures = {}
    for filename, stage in CAPTURES:
        path = tests / filename
        raw, observed_identity = stable_read(path, 2 * 1024 * 1024)
        snapshots[path] = raw, observed_identity
        receipt_path = output / (stage + ".log")
        receipt_raw, receipt_identity = stable_read(receipt_path, 65536)
        snapshots[receipt_path] = receipt_raw, receipt_identity
        receipt = _object(receipt_raw)
        expected = {
            "artifact": "test-results/" + filename,
            "sha256": _sha(raw),
            "native_baseline_admission": False,
            "execution_authority": False,
        }
        if filename.startswith("prospective-"):
            expected["g6_qualified"] = False
        if not native_migration_json_equal(receipt, expected):
            raise ValueError("capture_receipt_changed")
        value = _object(raw)
        if filename.startswith("prospective-"):
            verify_prospective_cutover_native_catalog_capture(
                value,
                migration_sha256=migration,
                manifest_sha256=identity["manifest_sha256"],
                source_files=files,
                require_native_baseline=False,
            )
        elif filename.startswith("account-history-"):
            verify_history_partition_native_catalog_capture_0051(
                value,
                migration_sha256=migration,
                manifest_sha256=identity["manifest_sha256"],
                source_files=files,
            )
        else:
            verify_coverage_native_catalog_capture_0051(
                value, migration_sha256=migration
            )
        raw_captures[filename] = raw
    for path, (raw, observed_identity) in snapshots.items():
        repeated, repeated_identity = stable_read(path, len(raw))
        if repeated != raw or repeated_identity != observed_identity:
            raise ValueError("capture_post_barrier_input_changed")
    return raw_captures, snapshots


def finalize_prospective_capture(
    *,
    output,
    export,
    tests,
    resources,
    run_id,
    files,
    identity,
    steps,
    archive_path,
    archive_raw,
    clock=time.monotonic,
    command=None,
):
    """Actual cleanup→independent absence→validation→export, no DTO permit input."""
    state = {"deadline": None}
    try:
        settlement = settle_owned_capture_writers(
            resources, run_id, state, clock=clock, command=command
        )
    except Exception as error:  # noqa: BLE001 -- even unexpected cleanup failures retain metadata.
        settlement = {
            "verified_absent": False,
            "failure_category": type(error).__name__,
            "shared_budget_seconds": CLEANUP_SECONDS,
            "shared_deadline": state["deadline"],
        }
    identity["capture_writer_settlement"] = settlement
    identity["writable_capture_reads_permitted"] = (
        settlement.get("verified_absent") is True
    )
    identity["trusted_capture_artifacts"] = None
    identity["unaccepted_capture_paths"] = [
        "test-results/" + name for name, _ in CAPTURES
    ]
    identity["capture_export_accepted"] = False
    identity["unaccepted_capture_export_path"] = "native-catalog-capture"
    identity["native_baseline_admission"] = False
    identity["g6_qualified"] = False
    if settlement.get("verified_absent") is not True:
        identity["status"] = "FAIL"
        identity["capture_custody_failure_category"] = "OwnedWriterAbsenceUnverified"
    else:
        try:
            captures, snapshots = _validated_capture_bytes(
                output, tests, files, identity, archive_path, archive_raw
            )
            # Stage immutable RAM bytes outside export. Failure preserves this
            # directory privately without publishing a trusted capture inventory.
            staging = output / "capture-export-staging"
            staging.mkdir(exist_ok=False)
            for filename, raw in captures.items():
                _write(staging / filename, raw)
            for path, (raw, observed_identity) in snapshots.items():
                repeated, repeated_identity = stable_read(path, len(raw))
                if repeated != raw or repeated_identity != observed_identity:
                    raise ValueError("capture_post_barrier_input_changed")
            target = export / "native-catalog-capture"
            # Exclusive directory/file creation never replaces earlier output.
            # A later failure keeps partial bytes explicitly unaccepted.
            target.mkdir(exist_ok=False)
            accepted = {}
            for filename, raw in captures.items():
                _write(target / filename, raw)
                published, _ = stable_read(target / filename, len(raw))
                if published != raw:
                    raise ValueError("capture_export_readback_changed")
                accepted["native-catalog-capture/" + filename] = _sha(raw)
            # A mutation during even the final publication/readback interval
            # cannot receive a trusted inventory. Retain any partial exports.
            for path, (raw, observed_identity) in snapshots.items():
                repeated, repeated_identity = stable_read(path, len(raw))
                if repeated != raw or repeated_identity != observed_identity:
                    raise ValueError("capture_post_barrier_input_changed")
            identity["trusted_capture_artifacts"] = accepted
            identity["unaccepted_capture_paths"] = []
            identity["capture_export_accepted"] = True
            identity["unaccepted_capture_export_path"] = None
            identity["captured_catalogs_are_observations_not_native_admission"] = True
        except Exception as error:  # noqa: BLE001 -- preserve failure without raw Docker/SQL text.
            identity["status"] = "FAIL"
            identity["capture_custody_failure_category"] = type(error).__name__
    _json_write(export / "cleanup.json", settlement)
    _json_write(export / "steps.json", steps)
    _json_write(export / "identity.json", identity)
    metadata = {
        name: _sha((export / name).read_bytes())
        for name in ("cleanup.json", "steps.json", "identity.json")
    }
    receipt = {
        "schema": "ctcc.prospective_cutover_capture_custody_receipt.v3",
        "status": identity["status"],
        "metadata_artifacts": metadata,
        "capture_artifacts": identity["trusted_capture_artifacts"],
        "native_baseline_admission": False,
        "g6_qualified": False,
        "execution_authority": False,
    }
    return _json_write(export / "receipt.json", receipt)
