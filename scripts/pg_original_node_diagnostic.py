"""Two fixed isolated PostgreSQL diagnostics; no trading or full acceptance.

The controller is stdlib only. Its contained sampler imports asyncpg lazily and
executes read-only observations against the generated, credential-free cluster.
Artifacts are not inspected until all labelled run writers are verified absent.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

PROFILES = {
    "partition-parent-writer-nowait-current": (
        "tests/integration/test_account_history_partition_repository.py",
        "test_partition_parent_writer_nowait_refusal_retains_head_and_rows",
    ),
    "submission-transport-ack-0041": (
        "tests/integration/test_submission_transport_denial_migration.py",
        "test_new_acknowledged_response_is_retained_only_as_ineligible_evidence[0041]",
    ),
}
CASE_SECONDS = 300
FAULT_SECONDS = 30
RESULTS = Path("/validation-results")
PHASE_FILE = "original-node-phases.jsonl"
SAMPLE_FILE = "original-node-locks.jsonl"
FINAL_FILE = "original-node-final-locks.jsonl"
JUNIT_FILE = "original-node.xml"
MAX_ROWS = 2048
MAX_SAMPLES = 151
MAX_SAMPLE_BYTES = 16 * 1024 * 1024
MAX_TRACE_BYTES = 4 * 1024 * 1024
MAX_PHASE_BYTES = 256 * 1024
REQUIRED_STAGES = (
    "build",
    "image-identity",
    "network-create",
    "postgres-start",
    "copied-manifest",
    "copied-source-exact",
    "pg-original-upgrade",
    "pg-original-migration-identity",
    "pg-original-observer-start",
    "pg-original-case",
)
LOCK_QUERY = """
SELECT jsonb_build_object(
 'database_observed_at',pg_catalog.clock_timestamp()::text,
 'observer_pid',pg_catalog.pg_backend_pid(),
 'rows',coalesce((SELECT jsonb_agg(to_jsonb(r)) FROM (
   SELECT a.pid,a.datid::bigint database_oid,a.datname,a.application_name,
     a.backend_type,a.state,a.backend_start::text,a.xact_start::text,
     a.query_start::text,a.wait_event_type,a.wait_event,
     pg_catalog.pg_blocking_pids(a.pid) blocking_pids,
     l.locktype,l.database::bigint lock_database_oid,l.relation::bigint relation_oid,
     l.mode,l.granted,l.virtualtransaction,l.transactionid::text,l.classid::bigint,
     l.objid::bigint,l.objsubid
   FROM pg_catalog.pg_stat_activity a
   LEFT JOIN pg_catalog.pg_locks l ON l.pid=a.pid
   WHERE a.datname='ctcc'
     OR a.datname ~ '^(ctcc_hp50_|ctcc_migration_test_)[a-f0-9]{32}$'
   ORDER BY a.datname,a.pid,l.locktype,l.database,l.relation,l.mode
   LIMIT 2049) r),'[]'::jsonb))
"""


def node_id(profile):
    if type(profile) is not str or profile not in PROFILES:
        raise ValueError("pg_original_profile_invalid")
    path, name = PROFILES[profile]
    return path + "::" + name


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _object(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("pg_original_duplicate_key")
            value[key] = item
        return value

    return json.loads(
        raw,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(
            ValueError("pg_original_nonfinite")
        ),
    )


def _json(raw):
    return (
        json.dumps(raw, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
        + b"\n"
    )


def _budget(state, clock):
    from scripts.prospective_capture_custody import _deadline

    end = _deadline(state, clock)
    if clock() >= end:
        raise TimeoutError("pg_original_shared_cleanup_deadline")
    return end - clock()


def _emit(path, value):
    raw = _json(value)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return _sha(raw)


def _base_args(name, *, run_id, network, image, tests, postgres, profile):
    if (
        re.fullmatch(r"[a-f0-9]{12}", run_id) is None
        or name not in {"pg-original-case", "pg-original-observer", "pg-original-final"}
        or network != f"ctcc-final-{run_id}-network"
        or postgres != f"ctcc-final-{run_id}-postgres"
        or re.fullmatch(r"sha256:[a-f0-9]{64}", image) is None
    ):
        raise ValueError("pg_original_owned_identity_invalid")
    node_id(profile)
    return [
        "docker",
        "run",
        "--name",
        f"ctcc-final-{run_id}-{name}",
        "--label",
        f"org.ctcc.validation.run={run_id}",
        "--network",
        network,
        "--mount",
        f"type=bind,source={tests.resolve()},target=/validation-results",
        "-e",
        "ENVIRONMENT=test",
        "-e",
        "TRADING_MODE=analysis_only",
        "-e",
        "LIVE_TRADING=false",
        "-e",
        "OKX_LIVE_ALLOW_ORDER_WRITES=false",
        "-e",
        "OKX_DEMO_ALLOW_ORDER_WRITES=false",
        "-e",
        "OKX_DEMO_AUTO_EXECUTION=false",
        "-e",
        f"DATABASE_URL=postgresql+asyncpg://ctcc@{postgres}:5432/ctcc",
        "-e",
        f"CTCC_PG_DIAGNOSTIC_PROFILE={profile}",
        "-e",
        f"CTCC_PG_DIAGNOSTIC_RUN_ID={run_id}",
        "-e",
        "PYTHONFAULTHANDLER=1",
    ]


def run_original_node(
    container,
    run,
    *,
    profile,
    resources,
    run_id,
    network,
    postgres,
    image,
    tests,
    state,
):
    """Existing300s case cap; no mutable node selection or copied test."""
    node = node_id(profile)
    container("pg-original-upgrade", ["alembic", "upgrade", "head"], timeout=120)
    container(
        "pg-original-migration-identity",
        ["python", "-m", "scripts.verify_migration_identity"],
        timeout=60,
    )
    common = {
        "run_id": run_id,
        "network": network,
        "image": image,
        "tests": tests,
        "postgres": postgres,
        "profile": profile,
    }
    observer = f"ctcc-final-{run_id}-pg-original-observer"
    resources.append(("container", observer))
    args = _base_args("pg-original-observer", **common)
    state["observer_attempted"] = True
    run.command(
        "pg-original-observer-start",
        [
            *args[:2],
            "-d",
            *args[2:],
            image,
            "python",
            "-B",
            "-m",
            "scripts.pg_original_node_diagnostic",
            "--sample",
        ],
        timeout=30,
    )
    case = f"ctcc-final-{run_id}-pg-original-case"
    resources.append(("container", case))
    args = _base_args("pg-original-case", **common)
    state["case_attempted"] = True
    run.command(
        "pg-original-case",
        [
            *args,
            image,
            "python",
            "-B",
            "-m",
            "scripts.hermetic_pytest",
            "-p",
            "no:cacheprovider",
            "-p",
            "scripts.pg_original_node_trace",
            "-rA",
            "-vv",
            "-x",
            "--tb=long",
            "--durations=0",
            "-o",
            f"faulthandler_timeout={FAULT_SECONDS}",
            f"--junitxml=/validation-results/{JUNIT_FILE}",
            node,
        ],
        timeout=CASE_SECONDS,
    )


def _assert_private_trace(raw):
    # No host credentials enter the isolated cluster. This final additional
    # export check still refuses recognizable private headers/credential URLs.
    if re.search(
        rb"(?i)(OK-ACCESS-(?:KEY|SIGN|PASSPHRASE)|Authorization\s*:\s*(?:Bearer|Basic)|(?:ntn_|sk-)[A-Za-z0-9_-]{24,}|://[^\s/:@]+:[^\s/@]+@)",
        raw,
    ):
        raise ValueError("pg_original_private_material_refused")


def phase_result(raw, *, profile):
    rows = [_object(line) for line in raw.splitlines()]
    if not 3 <= len(rows) <= 32:
        raise ValueError("pg_original_phase_inventory_incomplete")
    prior_mono = prior_utc = -1
    starts, reports = set(), {}
    for index, row in enumerate(rows):
        if (
            type(row) is not dict
            or row.get("schema") != "ctcc.pg_original_node_phase_event.v1"
            or row.get("profile") != profile
            or row.get("nodeid") != node_id(profile)
        ):
            raise ValueError("pg_original_phase_identity_invalid")
        if (
            type(row.get("sequence")) is not int
            or row["sequence"] != index + 1
            or type(row.get("monotonic_ns")) is not int
            or type(row.get("utc_ns")) is not int
            or row["monotonic_ns"] < prior_mono
            or row["utc_ns"] < prior_utc
        ):
            raise ValueError("pg_original_phase_clock_or_sequence_invalid")
        prior_mono, prior_utc = row["monotonic_ns"], row["utc_ns"]
        event = row.get("event")
        if event == "phase_start":
            when = row.get("when")
            if when not in {"setup", "call", "teardown"} or when in starts:
                raise ValueError("pg_original_phase_start_duplicate")
            starts.add(when)
        elif event == "phase_report":
            when, duration, outcome = (
                row.get("when"),
                row.get("duration_seconds"),
                row.get("outcome"),
            )
            if (
                when not in starts
                or when in reports
                or outcome not in {"passed", "failed", "skipped"}
                or type(duration) not in {int, float}
                or not math.isfinite(duration)
                or duration < 0
            ):
                raise ValueError("pg_original_phase_report_invalid")
            reports[when] = outcome
        elif event not in {
            "session_start",
            "node_start",
            "node_finish",
            "session_finish",
        }:
            raise ValueError("pg_original_phase_event_invalid")
    complete = (
        rows[0]["event"] == "session_start"
        and rows[-1]["event"] == "session_finish"
        and type(rows[-1].get("exitstatus")) is int
        and rows[-1]["exitstatus"] == 0
        and reports == {"setup": "passed", "call": "passed", "teardown": "passed"}
        and sum(row["event"] == "node_start" for row in rows) == 1
        and sum(row["event"] == "node_finish" for row in rows) == 1
        and sum(row["event"] == "session_start" for row in rows) == 1
        and sum(row["event"] == "session_finish" for row in rows) == 1
    )
    return {"complete_pass": complete, "events": len(rows), "phase_reports": reports}


def junit_result(raw, *, profile):
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("pg_original_junit_external_entity")
    root = ET.fromstring(raw)
    suites = [root] if root.tag == "testsuite" else list(root)
    if (
        root.tag not in {"testsuites", "testsuite"}
        or len(suites) != 1
        or suites[0].tag != "testsuite"
    ):
        raise ValueError("pg_original_junit_schema")
    suite = suites[0]
    cases = suite.findall("testcase")
    path, name = PROFILES[profile]
    if (
        len(cases) != 1
        or list(root.iter("testcase")) != cases
        or cases[0].get("classname") != path.removesuffix(".py").replace("/", ".")
        or cases[0].get("name") != name
    ):
        raise ValueError("pg_original_junit_original_case_required")
    counts = {
        key: int(suite.get(key, "-1"))
        for key in ("tests", "failures", "errors", "skipped")
    }
    if counts["tests"] != 1 or any(value < 0 for value in counts.values()):
        raise ValueError("pg_original_junit_counts")
    actual = {
        key: len(cases[0].findall(tag))
        for key, tag in (
            ("failures", "failure"),
            ("errors", "error"),
            ("skipped", "skipped"),
        )
    }
    if any(counts[key] != actual[key] for key in actual):
        raise ValueError("pg_original_junit_counts_conflict")
    return {"complete_pass": not any(actual.values()), **counts}


def samples_result(raw, *, profile, final):
    rows = [_object(line) for line in raw.splitlines()]
    if not rows or len(rows) > (1 if final else MAX_SAMPLES):
        raise ValueError("pg_original_lock_samples_incomplete")
    prior = -1
    for sequence, row in enumerate(rows, start=1):
        if (
            row.get("schema") != "ctcc.pg_original_node_lock_observation.v1"
            or row.get("profile") != profile
            or row.get("final") is not final
            or type(row.get("sequence")) is not int
            or row["sequence"] != sequence
            or type(row.get("monotonic_ns")) is not int
            or row["monotonic_ns"] < prior
        ):
            raise ValueError("pg_original_lock_sample_identity")
        prior = row["monotonic_ns"]
        if row.get("status") != "OBSERVED" or type(row.get("observation")) is not dict:
            raise ValueError("pg_original_lock_sample_unavailable")
        observed = row["observation"]
        if (
            type(observed.get("observer_pid")) is not int
            or observed["observer_pid"] < 1
            or type(observed.get("rows")) is not list
            or not 1 <= len(observed["rows"]) <= MAX_ROWS
        ):
            raise ValueError("pg_original_lock_sample_bound")
        for item in observed["rows"]:
            if (
                type(item) is not dict
                or not (
                    item.get("datname") == "ctcc"
                    or re.fullmatch(
                        r"(?:ctcc_hp50_|ctcc_migration_test_)[a-f0-9]{32}",
                        item.get("datname", ""),
                    )
                )
                or "relation_name" in item
            ):
                raise ValueError("pg_original_child_database_identity")
    return {
        "observations": len(rows),
        "complete": True,
        "relation_names_resolved_in_parent": False,
    }


def finalize_original_node(
    *,
    output,
    export,
    tests,
    resources,
    run_id,
    files,
    identity,
    steps,
    profile,
    network,
    postgres,
    image,
    state,
    clock=time.monotonic,
    command=None,
):
    """Final read-only snapshot, shared90s settlement, THEN trust stable bytes."""
    from scripts.prospective_capture_custody import (
        settle_owned_capture_writers,
        stable_read,
    )

    command = subprocess.run if command is None else command
    state.setdefault("deadline", None)
    settlement = {"verified_absent": False}
    evidence = {}
    stable_forensics = None
    identity["original_node_outcome"] = "INCOMPLETE"
    identity["accepted_original_node_cases"] = 0
    try:
        if state.get("observer_attempted"):
            try:
                remaining = _budget(state, clock)
                name = f"ctcc-final-{run_id}-pg-original-final"
                resources.append(("container", name))
                args = _base_args(
                    "pg-original-final",
                    run_id=run_id,
                    network=network,
                    image=image,
                    tests=tests,
                    postgres=postgres,
                    profile=profile,
                )
                # A timeout may leave this owned observer alive. The same state
                # settles it before any log/sample is read or trusted.
                with (output / "pg-original-final-observer.log").open("xb") as stream:
                    result = command(
                        [
                            *args,
                            image,
                            "python",
                            "-B",
                            "-m",
                            "scripts.pg_original_node_diagnostic",
                            "--sample-final",
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=stream,
                        stderr=subprocess.STDOUT,
                        timeout=min(10, remaining),
                        check=False,
                    )
                identity["final_observer_exit_code"] = result.returncode
                _budget(state, clock)
            except Exception as error:  # noqa: BLE001 -- fixed type only, never driver content.
                identity["final_observer_failure_type"] = type(error).__name__
                identity["status"] = "FAIL"
        if ("container", postgres) in resources:
            try:
                remaining = _budget(state, clock)
                inspected = command(
                    [
                        "docker",
                        "container",
                        "inspect",
                        "--format",
                        "{{json .Config.Labels}}",
                        postgres,
                    ],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=min(5, remaining),
                    check=False,
                )
                if (
                    inspected.returncode != 0
                    or type(inspected.stdout) is not bytes
                    or len(inspected.stdout) > 65536
                    or _object(inspected.stdout).get("org.ctcc.validation.run")
                    != run_id
                ):
                    raise ValueError("pg_original_postgres_log_owner_invalid")
                remaining = _budget(state, clock)
                # Capture bytes now, without reading or trusting them. Actual
                # server logs are checked only after all owned writers vanish.
                with (output / "pg-original-postgres.log").open("xb") as stream:
                    result = command(
                        ["docker", "logs", postgres],
                        stdin=subprocess.DEVNULL,
                        stdout=stream,
                        stderr=subprocess.STDOUT,
                        timeout=min(10, remaining),
                        check=False,
                    )
                identity["postgres_log_capture_exit_code"] = result.returncode
                _budget(state, clock)
            except Exception as error:  # noqa: BLE001 -- no Docker/PG text in exported metadata.
                identity["postgres_log_capture_failure_type"] = type(error).__name__
                identity["status"] = "FAIL"
    finally:
        try:
            settlement = settle_owned_capture_writers(
                resources, run_id, state, clock=clock, command=command
            )
        except Exception as error:  # noqa: BLE001 -- unknown cleanup forbids every raw read.
            settlement = {
                "verified_absent": False,
                "failure_category": type(error).__name__,
                "shared_budget_seconds": 90,
                "shared_deadline": state["deadline"],
            }
    identity["owned_writers_verified_absent"] = settlement["verified_absent"]
    identity.update(
        full_postgresql_acceptance=False,
        native_baseline_admission=False,
        g6_qualified=False,
        execution_authority=False,
    )
    if not settlement["verified_absent"]:
        identity["status"] = "FAIL"
        identity["artifact_reads_refused"] = True
    else:
        try:
            _budget(state, clock)
            if (
                identity.get("node_id") != node_id(profile)
                or identity.get("node_source_sha256")
                != files[PROFILES[profile][0]]["sha256"]
            ):
                raise ValueError("pg_original_source_node_identity_changed")
            allowed = {PHASE_FILE, SAMPLE_FILE, FINAL_FILE, JUNIT_FILE}
            names = {path.name for path in tests.iterdir()}
            if not names <= allowed:
                raise ValueError("pg_original_unexpected_artifact")
            inputs = [
                (output / "pg-original-case.log", MAX_TRACE_BYTES),
                (output / "pg-original-migration-identity.log", 65536),
            ]
            if (output / "pg-original-postgres.log").exists():
                inputs.append((output / "pg-original-postgres.log", MAX_TRACE_BYTES))
            inputs += [
                (
                    tests / name,
                    MAX_PHASE_BYTES
                    if name == PHASE_FILE
                    else MAX_TRACE_BYTES
                    if name == JUNIT_FILE
                    else MAX_SAMPLE_BYTES,
                )
                for name in sorted(names)
            ]
            retained = []
            for path, maximum in inputs:
                _budget(state, clock)
                raw, pin = stable_read(path, maximum)
                _assert_private_trace(raw)
                retained.append((path, raw, pin))
            evidence = {path.name: raw for path, raw, _ in retained}
            migration = _object(evidence["pg-original-migration-identity.log"])
            if (
                migration.get("schema") != "ctcc.migration_identity.v1"
                or migration.get("status") != "PASS"
                or migration.get("source_head") != "0051"
                or migration.get("database_head") != "0051"
                or migration.get("execution_authority") is not False
            ):
                raise ValueError("pg_original_migration_identity")
            identity["current_source_migration"] = {
                key: migration[key]
                for key in ("source_head", "database_head", "migration_sha256")
            }
            # Export actual traceback even when the original test FAILED. A
            # missing or malformed JUnit/phase report remains FAIL/INCOMPLETE.
            for path, raw, _ in retained:
                _budget(state, clock)
                with (export / path.name).open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                published, _ = stable_read(export / path.name, len(raw))
                if published != raw:
                    raise ValueError("pg_original_export_readback_changed")
            for path, raw, pin in retained:
                repeated, repeated_pin = stable_read(path, len(raw))
                if repeated != raw or repeated_pin != pin:
                    raise ValueError("pg_original_artifact_changed")
            _budget(state, clock)
            stable_forensics = {path.name: _sha(raw) for path, raw, _ in retained}
            phases = phase_result(evidence[PHASE_FILE], profile=profile)
            junit = junit_result(evidence[JUNIT_FILE], profile=profile)
            samples = samples_result(
                evidence[SAMPLE_FILE], profile=profile, final=False
            )
            final = samples_result(evidence[FINAL_FILE], profile=profile, final=True)
            case_steps = [
                step for step in steps if step.get("name") == "pg-original-case"
            ]
            identity["original_node_result"] = {
                "nodeid": node_id(profile),
                "phases": phases,
                "junit": junit,
                "samples": samples,
                "final_samples": final,
            }
            if junit["failures"] or junit["errors"]:
                identity["original_node_outcome"] = "FAILED"
            all_steps = all(
                len([step for step in steps if step.get("name") == name]) == 1
                and next(step for step in steps if step.get("name") == name).get(
                    "exit_code"
                )
                == 0
                for name in REQUIRED_STAGES
            )
            if (
                not all_steps
                or len(case_steps) != 1
                or case_steps[0].get("exit_code") != 0
                or not phases["complete_pass"]
                or not junit["complete_pass"]
                or identity.get("final_observer_exit_code") != 0
                or identity.get("postgres_log_capture_exit_code") != 0
            ):
                raise ValueError("pg_original_required_execution_incomplete")
            _budget(state, clock)
            identity["original_node_outcome"] = "PASS"
            identity["accepted_original_node_cases"] = 1
        except Exception as error:  # noqa: BLE001 -- original raw safe trace stays retained.
            identity["status"] = "FAIL"
            identity["evidence_failure_type"] = type(error).__name__
    # Metadata itself remains useful if writers cannot be quiesced; it is not
    # an acceptance of any uninspected file. Never scan raw output in that case.
    identity["stable_forensic_artifacts"] = stable_forensics
    hashes = {
        "cleanup.json": _emit(export / "cleanup.json", settlement),
        "steps.json": _emit(export / "steps.json", steps),
        "identity.json": _emit(export / "identity.json", identity),
    }
    return _emit(
        export / "receipt.json",
        {
            "schema": "ctcc.postgres_original_node_diagnostic_receipt.v1",
            "status": identity["status"],
            "metadata_artifacts": hashes,
            "stable_forensic_artifacts": stable_forensics,
            "original_node_outcome": identity["original_node_outcome"],
            "full_postgresql_acceptance": False,
            "native_baseline_admission": False,
            "g6_qualified": False,
            "execution_authority": False,
        },
    )


def _append_observation(stream, value, total):
    raw = _json(value)
    if total + len(raw) > MAX_SAMPLE_BYTES:
        raise ValueError("pg_original_sample_bytes_bound")
    stream.write(raw)
    stream.flush()
    os.fsync(stream.fileno())
    return total + len(raw)


async def sample_locks(*, final=False):
    import asyncpg

    profile = os.environ.get("CTCC_PG_DIAGNOSTIC_PROFILE", "")
    node_id(profile)
    run_id = os.environ.get("CTCC_PG_DIAGNOSTIC_RUN_ID", "")
    if re.fullmatch(r"[a-f0-9]{12}", run_id) is None:
        raise ValueError("pg_original_sample_scope_invalid")
    host = f"ctcc-final-{run_id}-postgres"
    if os.environ.get("DATABASE_URL") != f"postgresql+asyncpg://ctcc@{host}:5432/ctcc":
        raise ValueError("pg_original_sample_database_invalid")
    target = RESULTS / (FINAL_FILE if final else SAMPLE_FILE)
    started = time.monotonic()
    total = 0
    connection = None
    with target.open("xb") as stream:
        try:
            connection = await asyncpg.connect(
                host=host,
                port=5432,
                user="ctcc",
                database="ctcc",
                timeout=2,
                command_timeout=2,
                server_settings={
                    "search_path": "pg_catalog",
                    "TimeZone": "UTC",
                    "application_name": "ctcc-pg-diagnostic-observer",
                },
            )
            for sequence in range(1, 2 if final else MAX_SAMPLES + 1):
                if time.monotonic() - started >= CASE_SECONDS:
                    break
                async with connection.transaction(readonly=True):
                    raw = await connection.fetchval(LOCK_QUERY)
                    observation = _object(raw)
                if (
                    type(observation) is not dict
                    or type(observation.get("rows")) is not list
                    or not 1 <= len(observation["rows"]) <= MAX_ROWS
                ):
                    raise ValueError("pg_original_sample_bound")
                total = _append_observation(
                    stream,
                    {
                        "schema": "ctcc.pg_original_node_lock_observation.v1",
                        "profile": profile,
                        "final": final,
                        "sequence": sequence,
                        "utc_ns": time.time_ns(),
                        "monotonic_ns": time.monotonic_ns(),
                        "status": "OBSERVED",
                        "observation": observation,
                    },
                    total,
                )
                if final:
                    break
                await asyncio.sleep(2)
        except Exception as error:  # noqa: BLE001 -- retain observation failure without raw SQL.
            _append_observation(
                stream,
                {
                    "schema": "ctcc.pg_original_node_lock_observation.v1",
                    "profile": profile,
                    "final": final,
                    "sequence": 0,
                    "utc_ns": time.time_ns(),
                    "monotonic_ns": time.monotonic_ns(),
                    "status": "UNAVAILABLE",
                    "failure_type": type(error).__name__,
                },
                total,
            )
            return 1
        finally:
            if connection is not None:
                await connection.close(timeout=2)
    return 0


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sample", action="store_true")
    group.add_argument("--sample-final", action="store_true")
    args = parser.parse_args()
    return asyncio.run(sample_locks(final=args.sample_final))


if __name__ == "__main__":
    raise SystemExit(main())
