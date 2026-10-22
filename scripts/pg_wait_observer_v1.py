"""Independent read-only observer of the one disposable CI cluster; no raw SQL output."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from scripts.pg_wait_redaction_v1 import PgWaitFormatterV1, PgWaitPolicyV1

ROOT = Path("/validation-results")
COMMANDS = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "CREATE",
    "ALTER",
    "DROP",
    "LOCK",
    "GRANT",
    "REVOKE",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "SET",
    "SHOW",
    "DO",
    "WITH",
    "SAVEPOINT",
    "RELEASE",
    "RESET",
    "DISCARD",
    "START",
    "END",
)
# Raw query text, usename and connection strings never leave PostgreSQL here.
SQL = """
SELECT pid, datname, backend_type, state, wait_event_type, wait_event,
       pg_catalog.pg_blocking_pids(pid) AS blocking_pids,
       pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(query,'UTF8')),'hex') AS query_sha256,
       pg_catalog.octet_length(query) AS query_bytes,
       CASE WHEN pg_catalog.upper(pg_catalog.substring(query,'^[[:space:]]*([A-Za-z]+)'))
                 = ANY($1::text[])
            THEN pg_catalog.upper(pg_catalog.substring(query,'^[[:space:]]*([A-Za-z]+)'))
            ELSE 'UNKNOWN' END AS command_category
FROM pg_catalog.pg_stat_activity
WHERE pid <> pg_catalog.pg_backend_pid() AND backend_type='client backend'
  AND datname IS NOT NULL AND datname LIKE 'ctcc%'
ORDER BY pid
LIMIT 129
"""


def write_new(path, value):
    raw = (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()
    with path.open("xb") as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())


async def observe():
    import asyncpg

    experiment = os.environ.get("CTCC_PG_PHASE_EXPERIMENT")
    if experiment not in ("A", "B"):
        raise ValueError("OBSERVER_CONFIGURATION_DENIED")
    url = os.environ.get("DATABASE_URL")
    if type(url) is not str:
        raise ValueError("OBSERVER_CONFIGURATION_DENIED")
    parsed = urlsplit(url)
    if not (
        parsed.scheme == "postgresql+asyncpg"
        and parsed.username == "ctcc"
        and parsed.password is None
        and parsed.port == 5432
        and re.fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", parsed.hostname or "")
        and parsed.path == "/ctcc"
        and not parsed.query
        and not parsed.fragment
    ):
        raise ValueError("OBSERVER_CONFIGURATION_DENIED")
    duration = 210 if experiment == "A" else 630
    formatter = PgWaitFormatterV1(
        run_secret=secrets.token_bytes(32),
        policy=PgWaitPolicyV1(
            max_samples=64,
            max_rows=128,
            max_blockers_per_row=64,
            max_query_bytes=1048576,
            max_duration_ns=duration * 1_000_000_000,
        ),
    )
    connection = None
    started = time.monotonic_ns()
    samples = 0
    journal_bytes = 0
    failure = False
    status = "FAIL"
    try:
        connection = await asyncpg.connect(
            url.replace("postgresql+asyncpg://", "postgresql://", 1),
            timeout=5,
            command_timeout=2,
            server_settings={
                "default_transaction_read_only": "on",
                "statement_timeout": "2000",
                "lock_timeout": "500",
                "application_name": "ctcc_pg_diagnostic_observer",
            },
        )
        with (ROOT / "wait-samples.jsonl").open("xb") as output:
            while True:
                elapsed = time.monotonic_ns() - started
                if elapsed >= duration * 1_000_000_000:
                    raise ValueError("OBSERVER_BUDGET_EXHAUSTED")
                if (ROOT / "observer-stop").exists():
                    status = "PASS" if samples > 0 and not failure else "FAIL"
                    break
                if samples >= 64:
                    raise ValueError("OBSERVER_BUDGET_EXHAUSTED")
                records = await connection.fetch(SQL, list(COMMANDS), timeout=2)
                raw = formatter.format_sample(
                    [dict(record) for record in records],
                    sample_index=samples,
                    elapsed_monotonic_ns=time.monotonic_ns() - started,
                )
                if journal_bytes + len(raw) + 1 >= 3 * 1024 * 1024:
                    raise ValueError("OBSERVER_AGGREGATE_BYTES_EXHAUSTED")
                output.write(raw + b"\n")
                output.flush()
                os.fsync(output.fileno())
                failure = failure or json.loads(raw)["status"] != "PASS"
                samples += 1
                journal_bytes += len(raw) + 1
                # Stop checks are bounded without a test-engine hook/listener.
                for _ in range(20):
                    if (ROOT / "observer-stop").exists():
                        break
                    await asyncio.sleep(0.5)
    except Exception:  # noqa: BLE001 - No native exception/SQL/identity text is exported.
        status = "FAIL"
    finally:
        sampling_elapsed = time.monotonic_ns() - started
        if sampling_elapsed >= duration * 1_000_000_000:
            status = "FAIL"
        if connection is not None:
            try:
                await asyncio.wait_for(connection.close(), timeout=3)
            except Exception:  # noqa: BLE001
                status = "FAIL"
        write_new(
            ROOT / "observer-result.json",
            {
                "schema": "ctcc.pg_wait_observer_result.v1",
                "status": status,
                "measured_sample_count": samples,
                "observations_complete": status == "PASS",
                "sampling_elapsed_monotonic_ns": sampling_elapsed,
                "sampling_budget_ns": duration * 1_000_000_000,
                "connection_close_grace_seconds": 3,
                "reason": "STOP_REQUEST_OBSERVED"
                if status == "PASS"
                else "OBSERVER_INCOMPLETE_OR_DENIED",
                "raw_sql_or_identity_exported": False,
                "execution_authority": False,
            },
        )
    return 0 if status == "PASS" else 1


def main():
    try:
        return asyncio.run(observe())
    except BaseException:  # noqa: BLE001 - Never publish a native traceback.
        print('{"status":"OBSERVER_FAILED_CLOSED","raw_payload_exported":false}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
