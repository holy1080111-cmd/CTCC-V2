"""Apply one attended, exact DB0056 owner ACL plan and read back the result."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import asdict
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import get_settings
from app.demo_automation import native_server_selectors_v2 as selectors
from app.domain.source_primitives import canonical, sha
from scripts import native_owner_grant_0056_draft_v1 as acl
from scripts import native_owner_grant_journal_0056_v1 as journals
from scripts import native_owner_postgrant_readback_0056_v1 as readback
from scripts import native_owner_target_plan_0056_v1 as target
from scripts.native_owner_grant_confirmation_0056_v1 import confirm_owner_grant_0056_v1
from scripts.native_owner_reviewed_source_0056_v1 import (
    require_exact_installed_owner_source_0056_v1,
)
from scripts.native_owner_route_preflight_0056_v1 import (
    _prepare as prepare_route,
)
from scripts.native_owner_route_preflight_0056_v1 import (
    preflight_owner_route_0056_v1,
)
from scripts.native_owner_upgrade_0056_v1 import OwnerUpgradePinsV1

_SOURCE = Path(__file__).resolve().parents[1]
_SCHEMA = "ctcc.native_owner_0056.attended_grant.v2"


class OwnerGrantAttemptNeedsReconciliation(RuntimeError):
    """A prior application attempt must be reconciled without granting again."""


def _stable_target_sha256(pins: acl.OwnerGrantDraftPinsV1) -> str:
    return sha(
        canonical(
            {
                "schema": "ctcc.native_owner_0056.physical_grant_target.v1",
                "cluster_system_identifier_sha256": pins.route.cluster_system_identifier_sha256,
                "database_oid": pins.route.database_oid,
            }
        )
    )


def _plan_sha256(pins: acl.OwnerGrantDraftPinsV1) -> str:
    return sha(canonical(asdict(pins)))


def _journal_arguments(pins: acl.OwnerGrantDraftPinsV1) -> dict[str, str]:
    return {
        "target_sha256": _stable_target_sha256(pins),
        "plan_sha256": _plan_sha256(pins),
        "source_archive_sha256": pins.source_archive_sha256,
    }


def _target_route(pins):
    return ("postgresql+asyncpg", pins.route.host, pins.route.port, pins.route.database)


async def _check_restricted_settings(pins) -> None:
    """Reject misconfigured future readback before opening any writer."""
    settings = get_settings()
    acl._need(settings.ctcc_native_pg_tls_ca_sha256 == pins.route.ca_sha256)
    selected = selectors.select_configured_native_demo_factories_v2()
    try:
        readback._configured_role_urls(selected, _target_route(pins), acl._names(pins))
    finally:
        await selectors._dispose_failed_selection_factories_v2(selected)


def _checked_source(pins, source_archive_file):
    source = OwnerUpgradePinsV1(
        pins.source_commit, pins.source_tree, pins.source_archive_sha256, pins.route
    )
    require_exact_installed_owner_source_0056_v1(_SOURCE, source, source_archive_file)
    return source


def _record_terminal(attempt, outcome: str) -> None:
    try:
        attempt.record_terminal(outcome)
    except Exception:  # noqa: BLE001 -- a durable STARTED already blocks replay.
        raise OwnerGrantAttemptNeedsReconciliation(
            "native_owner_0056_grant_attempt_requires_reconciliation"
        ) from None


async def apply_attended_owner_grant_0056_v2(
    *,
    plan_file: Path,
    plan_file_sha256: str,
    owner_dsn_file: Path,
    ca_file: Path,
    source_archive_file: Path,
    journal_directory: Path,
) -> dict[str, object]:
    """Run the owner-only ACL change. The invocation never grants order access."""
    # The shared native storage publisher fsyncs its directory on POSIX.
    if not _supports_durable_grant_journal():
        acl._need(False)
    pins = readback._load_plan(plan_file, plan_file_sha256)
    acl._names(pins)
    _checked_source(pins, source_archive_file)
    target._require_private_file(owner_dsn_file)
    target.require_private_evidence_base_v2(str(journal_directory))
    if (
        journals.load_existing(journal_directory, **_journal_arguments(pins))
        is not None
    ):
        raise OwnerGrantAttemptNeedsReconciliation(
            "native_owner_0056_grant_attempt_requires_reconciliation"
        )
    prepared = prepare_route(pins.route, owner_dsn_file=owner_dsn_file, ca_file=ca_file)
    acl._need(selectors._database_route(prepared.url) == _target_route(pins))
    await _check_restricted_settings(pins)
    profile = await preflight_owner_route_0056_v1(
        pins.route, owner_dsn_file=owner_dsn_file, ca_file=ca_file
    )
    acl._need(
        profile["status"] == "PASS"
        and profile["database_head"] == "0056"
        and profile["route_sha256"] == prepared.route_sha256
        and profile["startup_admission"] is profile["execution_authority"] is False
    )
    confirm_owner_grant_0056_v1(plan_file_sha256)
    acl._need(readback._load_plan(plan_file, plan_file_sha256) == pins)
    _checked_source(pins, source_archive_file)
    target._require_private_file(owner_dsn_file)
    # Reopen credential and pinned CA before the one-shot journal is created.
    confirmed = prepare_route(
        pins.route, owner_dsn_file=owner_dsn_file, ca_file=ca_file
    )
    acl._need(
        confirmed.url == prepared.url
        and confirmed.route_sha256 == prepared.route_sha256
        and confirmed.manifest_sha256 == prepared.manifest_sha256
        and confirmed.migration_sha256 == prepared.migration_sha256
    )

    engine = create_async_engine(
        confirmed.url,
        poolclass=NullPool,
        connect_args={"ssl": confirmed.tls, "timeout": 10},
        hide_parameters=True,
    )
    try:
        factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        async with asyncio.timeout(80):
            physical = await acl._read_before(factory, pins, reviewed=True)
        attempt = journals.load_or_begin(journal_directory, **_journal_arguments(pins))
        if not attempt.created:
            raise OwnerGrantAttemptNeedsReconciliation(
                "native_owner_0056_grant_attempt_requires_reconciliation"
            )
    except BaseException:
        await engine.dispose()
        raise
    try:
        await acl._commit_once(engine, pins, physical)
    except acl.OwnerGrantCommitUnknown:
        _record_terminal(attempt, "COMMIT_UNKNOWN")
        raise
    except acl.OwnerGrantPostcommitIncomplete:
        _record_terminal(attempt, "POSTCOMMIT_INCOMPLETE")
        raise
    except acl.OwnerGrantRefusal:
        _record_terminal(attempt, "REFUSED")
        raise
    except (Exception, asyncio.CancelledError):  # noqa: BLE001 -- after dispatch the state is uncertain.
        _record_terminal(attempt, "COMMIT_UNKNOWN")
        raise acl.OwnerGrantCommitUnknown(
            "native_owner_0056_grant_commit_unknown"
        ) from None

    try:
        receipt = await readback.observe_owner_postgrant_readback_0056_v1(
            pins,
            owner_dsn_file=owner_dsn_file,
            ca_file=ca_file,
            source_archive_file=source_archive_file,
        )
        _check_readback(receipt, pins)
    except (Exception, asyncio.CancelledError):  # noqa: BLE001 -- no retry after COMMIT.
        _record_terminal(attempt, "POSTCOMMIT_INCOMPLETE")
        raise acl.OwnerGrantPostcommitIncomplete(
            "native_owner_0056_grant_readback_required"
        ) from None
    _record_terminal(attempt, "OBSERVED_GRANTED")
    return {
        **receipt,
        "schema": _SCHEMA,
        "status": "GRANTED_AND_OBSERVED",
        "operator_confirmation_received": True,
        "grant_statements": 42,
    }


def _check_readback(receipt, pins):
    acl._need(
        type(receipt) is dict
        and receipt["status"] == "OBSERVED_DENY"
        and receipt["admission"] == "DENY"
        and receipt["target_grant_pins_sha256"] == _plan_sha256(pins)
        and receipt["restricted_logins_observed"] == 3
        and receipt["startup_admission"] is receipt["execution_authority"] is False
        and receipt["external_approval_authenticated"] is False
    )


def _supports_durable_grant_journal() -> bool:
    return sys.platform != "win32"


async def reconcile_owner_grant_0056_v2(
    *,
    plan_file: Path,
    plan_file_sha256: str,
    owner_dsn_file: Path,
    ca_file: Path,
    source_archive_file: Path,
    journal_directory: Path,
) -> dict[str, object]:
    """Verify the actual granted state independently, leaving the attempt closed."""
    pins = readback._load_plan(plan_file, plan_file_sha256)
    attempt = journals.load_existing(journal_directory, **_journal_arguments(pins))
    acl._need(attempt is not None)
    receipt = await readback.observe_owner_postgrant_readback_0056_v1(
        pins,
        owner_dsn_file=owner_dsn_file,
        ca_file=ca_file,
        source_archive_file=source_archive_file,
    )
    _check_readback(receipt, pins)
    reconciliation_sha256 = attempt.record_reconciliation("EXACT_GRANTED_ACL_OBSERVED")
    return {
        **receipt,
        "schema": _SCHEMA,
        "status": "EXACT_GRANTED_ACL_OBSERVED",
        "attempt_status": attempt.status,
        "reconciliation_sha256": reconciliation_sha256,
        "commit_attributed_to_attempt": attempt.status == "OBSERVED_GRANTED",
    }


class _PrivateParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        acl._need(False)


def main(argv: list[str] | None = None) -> int:
    parser = _PrivateParser(allow_abbrev=False)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--apply-reviewed-grant", action="store_true")
    action.add_argument("--reconcile-only", action="store_true")
    parser.add_argument("--plan-file", required=True, type=Path)
    parser.add_argument("--plan-file-sha256", required=True)
    parser.add_argument("--owner-dsn-file", required=True, type=Path)
    parser.add_argument("--ca-file", required=True, type=Path)
    parser.add_argument("--source-archive-file", required=True, type=Path)
    parser.add_argument("--journal-directory", required=True, type=Path)
    reconciling = False
    try:
        values = vars(parser.parse_args(argv))
        reconciling = values.pop("reconcile_only")
        values.pop("apply_reviewed_grant")
        operation = (
            reconcile_owner_grant_0056_v2
            if reconciling
            else apply_attended_owner_grant_0056_v2
        )
        result = asyncio.run(operation(**values))
    except (
        OwnerGrantAttemptNeedsReconciliation,
        acl.OwnerGrantCommitUnknown,
        acl.OwnerGrantPostcommitIncomplete,
        KeyboardInterrupt,
        asyncio.CancelledError,
    ):
        print(
            '{"admission":"DENY","status":"RECONCILIATION_REQUIRED"}', file=sys.stderr
        )
        return 3
    except Exception:  # noqa: BLE001 -- keep CLI inputs and database values private.
        status = "RECONCILIATION_INCONCLUSIVE" if reconciling else "REFUSED"
        print(
            canonical({"admission": "DENY", "status": status}).decode("ascii"),
            file=sys.stderr,
        )
        return 3 if reconciling else 1
    print(canonical(result).decode("ascii"))
    # Owner ACL administration never becomes startup or execution authority.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
