"""Owned normal-Demo journal hooks over the existing reservation/outcome ledger.

Every operation needs the native postsubmit registry's exact binding and held
account transaction. Public DATA objects, booleans, IDs and imported functions
alone cannot create a persistence source or release a reservation.
"""

import asyncio
from dataclasses import dataclass, field

from sqlalchemy import text

from app.database.repositories import native_postsubmit_v1 as journal
from app.domain import native_protective_hold_schema_v58 as schema
from app.domain import native_submission_binding_schema_v58 as submission
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import native_protective_exit_facts_v1 as facts


@dataclass(frozen=True, slots=True, repr=False)
class NativeProtectedHistoryV1:
    hold_outcome_id: str
    hold_json: bytes = field(repr=False)
    original_observations: tuple = field(repr=False)
    original_assessment_sha256: str
    read_metadata: tuple = field(repr=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


def protected_schema_selected_v1(binding):
    profile = journal._database_profile(binding)
    if profile is None:
        return False  # Existing independently checked physical55 adapter.
    head = profile[1].observation.database_head
    journal.need(head in ("0056", "0057", "0058"))
    return head == "0058"


async def _require_installed_schema(binding, session):
    """Exact installed predicate/trigger bodies; no source-only promotion."""
    rows = tuple(
        (
            await session.execute(
                text(
                    "SELECT p.proname,pg_catalog.oidvectortypes(p.proargtypes),"
                    "pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc,'UTF8')),'hex'),"
                    "p.prosecdef,p.proconfig FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n "
                    "ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname IN "
                    "('qualification_native_protective_flat_data_v58','qualification_reservation_update','qualification_submission_binding')"
                )
            )
        ).all()
    )
    if binding is not None:
        journal._source(binding)
    expected = {
        (
            schema.FUNCTION,
            schema.FUNCTION_IDENTITY,
            schema.PREDICATE_BODY_SHA256,
            False,
            ("search_path=pg_catalog, pg_temp",),
        ),
        (
            "qualification_reservation_update",
            "",
            schema.CURRENT_BODY_SHA256,
            False,
            ("search_path=pg_catalog, pg_temp",),
        ),
        (
            submission.FUNCTION,
            "",
            submission.CURRENT_BODY_SHA256,
            False,
            (),
        ),
    }
    journal.need(
        len(rows) == 3
        and {(row[0], row[1], row[2], row[3], tuple(row[4] or ())) for row in rows}
        == expected,
        "native_protective58_installed_schema_unavailable",
    )


async def assert_selected_protective_schema_before_arm_v1(selection):
    """Read-only early refusal; final held native writer rechecks independently."""
    from app.demo_automation import native_server_selectors_v2 as selectors

    original = selectors._record(selection)
    journal.need(
        selectors._runtime_selection_database_head_v2(selection) == "0058",
        "native_protected_entry_requires_actual0058",
    )
    factory = original["factories"].writer_factory
    async with asyncio.timeout(5), factory() as session, session.begin():
        await session.execute(text("SET TRANSACTION READ ONLY"))
        heads = tuple(
            (
                await session.scalars(
                    text("SELECT version_num FROM public.alembic_version LIMIT 2")
                )
            ).all()
        )
        journal.need(heads == ("0058",))
        await _require_installed_schema(None, session)
        journal.need(
            selectors._record(selection) is original
            and original["factories"].writer_factory is factory
        )


def _hold_document(state):
    original = state["normal_hold_original"]
    target = state["target"]
    journal.need(type(original) is tuple and len(original) == 4)
    assessment = original[1]
    journal.need(
        assessment.filled_and_protected is True
        and assessment.receipt_json == state["assessment"].receipt_json
    )
    facts.original_protection(target.facts, original[2])
    return {
        "version": schema.HOLD_VERSION,
        "reservation_id": target.reservation_id,
        "request_sha256": sha(target.request_json.encode()),
        "intent_sha256": target.intent.sha256,
        "original_filled_outcome_id": original[0],
        "original_manifest_outcome_id": state["durable_readback_manifest_id"],
        "original_assessment_sha256": sha(assessment.receipt_json),
        "original_event_key": target.request.origin.original_event_key,
        "risk_hold_retained": True,
        "entry_authority_revocation_pending": True,
        "execution_authority": False,
        "order_retry_authority": False,
    }


async def append_owned_protected_hold_v1(binding):
    """Append one actual filled lineage and reopen it in a distinct DB session."""
    state, _ = journal._source(binding)
    document = _hold_document(state)
    async with journal._transaction(binding) as parts:
        await _require_installed_schema(binding, parts[3])
        current = await journal._target_locked(
            binding, parts[1], parts[2], parts[3], parts[4], parts[-1]
        )
        journal.need(
            (current.current_state, current.current_state_revision) == ("uncertain", 3)
        )
        prior = await journal._outcomes(parts[3], state["target"])
        journal.need(
            not any(
                decode(row.capture_json.encode(), 140000).get("version")
                in {schema.HOLD_VERSION, "ctcc.native_controlled_close_intent.v1"}
                for row in prior
            )
        )
        outcome = await journal._append(binding, parts, document, kind="reconciliation")
        metadata = parts[-1]
    async with journal._transaction(binding) as parts:
        await _require_installed_schema(binding, parts[3])
        rows = await journal._outcomes(parts[3], state["target"])
        journal.need(
            parts[-1][8] != metadata[8]
            and rows[-1].outcome_id == outcome
            and decode(rows[-1].capture_json.encode(), 140000) == document
        )
        current = await journal._target_locked(
            binding, parts[1], parts[2], parts[3], parts[4], parts[-1]
        )
        journal.need(
            (current.current_state, current.current_state_revision) == ("uncertain", 3)
        )
    state["normal_hold_committed"] = (outcome, canonical(document), parts[-1])
    return outcome


async def read_owned_protected_history_v1(binding):
    """Two actual committed reads; no previous process owner is recreated."""
    state, _ = journal._source(binding)
    retained = None
    result = None
    first_xid = None
    for _ in range(2):
        async with journal._transaction(binding) as parts:
            await _require_installed_schema(binding, parts[3])
            rows = await journal._outcomes(parts[3], state["target"])
            matches = [
                (row, decode(row.capture_json.encode(), 140000))
                for row in rows
                if decode(row.capture_json.encode(), 140000).get("version")
                == schema.HOLD_VERSION
            ]
            if not matches:
                journal.need(result is None and retained is None)
                return None
            journal.need(len(matches) == 1)
            row, document = matches[0]
            journal.need(rows and rows[0].observation_kind == "initial")
            state["initial_outcome_id"] = rows[0].outcome_id
            raw = tuple(canonical(journal._row(item)) for item in rows)
            manifest, observations = journal._read_manifest(
                rows, document["original_manifest_outcome_id"]
            )
            original = journal.computation.assess_native_reconciliation_v1(
                state["target"].facts, observations
            )
            journal.need(
                original.filled_and_protected is True
                and sha(original.receipt_json)
                == document["original_assessment_sha256"]
                == manifest["assessment_sha256"]
            )
            journal.need(
                document["reservation_id"] == state["reservation_id"]
                and document["request_sha256"]
                == sha(state["target"].request_json.encode())
                and document["intent_sha256"] == state["target"].intent.sha256
            )
            if retained is not None:
                journal.need(retained == raw and first_xid != parts[-1][8])
            retained, first_xid = raw, parts[-1][8]
            result = NativeProtectedHistoryV1(
                row.outcome_id,
                canonical(document),
                observations,
                sha(original.receipt_json),
                parts[-1],
            )
            if state["target"].current_state == "reconciled_flat":
                closed = [
                    (item, decode(item.capture_json.encode(), 140000))
                    for item in rows
                    if decode(item.capture_json.encode(), 140000).get("version")
                    == schema.TERMINAL_VERSION
                ]
                journal.need(
                    len(closed) == 1
                    and closed[0][1]["hold_outcome_id"] == row.outcome_id
                )
                stored_row, witness = closed[0]
                _, terminal_pages = journal._read_manifest(
                    rows, witness["manifest_outcome_id"]
                )
                assessment = facts.assess_original_protective_exit_v1(
                    state["target"].facts, observations, terminal_pages
                )
                journal.need(
                    assessment.receipt_json.decode() == witness["assessment_json"]
                    and sha(assessment.receipt_json) == witness["assessment_sha256"]
                )
                journal.need(
                    await parts[3].scalar(
                        text(
                            "SELECT public.qualification_native_protective_flat_data_v58(:rid,:at)"
                        ),
                        {"rid": state["reservation_id"], "at": stored_row.recorded_at},
                    )
                    is True
                )
                state["verified_protective_flat_closure"] = (
                    stored_row.outcome_id,
                    assessment,
                    terminal_pages,
                    parts[-1],
                )
    state["normal_protected_history"] = result
    # Source's scanner replays these exact SQL-returned historical bytes. No
    # fresh HTTP or previous credential/Arm ownership is inferred from them.
    journal._RECOVERY_HISTORY_BUFFERS_V1[binding] = retained
    state["recovery_history_retained"] = retained
    return result


async def append_owned_protected_open_readback_v1(binding):
    state, _ = journal._source(binding)
    history = state["normal_protected_history"]
    journal.need(type(history) is NativeProtectedHistoryV1)
    actual = facts.assess_open_original_protection_v1(
        state["target"].facts,
        history.original_observations,
        state["durable_observations"],
    )
    manifest = {
        **state["durable_raw_manifest"],
        "computation": "completed",
        "assessment_sha256": sha(actual.receipt_json),
        "raw_manifest_outcome_id": state["durable_raw_manifest_id"],
    }
    async with journal._transaction(binding) as parts:
        await _require_installed_schema(binding, parts[3])
        manifest_id = await journal._append(
            binding, parts, manifest, kind="reconciliation"
        )
        outcome = await journal._append(
            binding,
            parts,
            {
                "version": "ctcc.native_protected_open_readback.v1",
                "hold_outcome_id": history.hold_outcome_id,
                "manifest_outcome_id": manifest_id,
                "assessment_json": actual.receipt_json.decode(),
                "assessment_sha256": sha(actual.receipt_json),
                "risk_hold_retained": True,
                "execution_authority": False,
                "order_retry_authority": False,
            },
            kind="reconciliation",
        )
        metadata = parts[-1]
    async with journal._transaction(binding) as parts:
        rows = await journal._outcomes(parts[3], state["target"])
        journal.need(rows[-1].outcome_id == outcome and parts[-1][8] != metadata[8])
        _, observations = journal._read_manifest(rows, manifest_id)
        journal.need(
            facts.assess_open_original_protection_v1(
                state["target"].facts, history.original_observations, observations
            )
            == actual
        )
        current = await journal._target_locked(
            binding, parts[1], parts[2], parts[3], parts[4], parts[-1]
        )
        journal.need(
            (current.current_state, current.current_state_revision) == ("uncertain", 3)
        )
    from app.trade_qualification.native_postsubmit_reconciliation_v1 import (
        NativeReconciliationResultV1,
    )

    result = NativeReconciliationResultV1(
        state["reservation_id"],
        "original_position_protected_hold_retained",
        outcome,
        sha(actual.receipt_json),
        True,
    )
    state["verified_normal_protected_recovery"] = (
        result,
        actual,
        observations,
        parts[-1],
    )
    return result


async def append_owned_protective_assessment_v1(binding):
    state, _ = journal._source(binding)
    history = state["normal_protected_history"]
    journal.need(type(history) is NativeProtectedHistoryV1)
    assessment = facts.assess_original_protective_exit_v1(
        state["target"].facts,
        history.original_observations,
        state["durable_observations"],
    )
    journal.need(assessment.receipt_json == state["protective_assessment"].receipt_json)
    manifest = {
        **state["durable_raw_manifest"],
        "computation": "completed",
        "assessment_sha256": sha(assessment.receipt_json),
        "raw_manifest_outcome_id": state["durable_raw_manifest_id"],
    }
    async with journal._transaction(binding) as parts:
        await _require_installed_schema(binding, parts[3])
        outcome = await journal._append(binding, parts, manifest, kind="reconciliation")
        metadata = parts[-1]
    async with journal._transaction(binding) as parts:
        rows = await journal._outcomes(parts[3], state["target"])
        journal.need(
            parts[-1][8] != metadata[8]
            and rows[-1].outcome_id == outcome
            and decode(rows[-1].capture_json.encode(), 140000) == manifest
        )
        _, observations = journal._read_manifest(rows, outcome)
        replay = facts.assess_original_protective_exit_v1(
            state["target"].facts, history.original_observations, observations
        )
        journal.need(replay == assessment)
    state["protective_durable_assessment"] = (
        outcome,
        assessment,
        observations,
        parts[-1],
    )
    return outcome


async def finish_owned_protective_exit_v1(binding):
    """Only full raw SQL predicate + committed independent replay resolve risk."""
    state, _ = journal._source(binding)
    target, history = state["target"], state["normal_protected_history"]
    terminal = state["protective_durable_assessment"]
    inner = decode(
        decode(target.intent.canonical_json.encode(), journal.wire.MAX_NATIVE_BYTES)[
            "intent_json"
        ].encode(),
        journal.wire.MAX_NATIVE_BYTES,
    )
    witness = {
        "version": schema.TERMINAL_VERSION,
        "reservation_id": target.reservation_id,
        "request_sha256": sha(target.request_json.encode()),
        "intent_sha256": target.intent.sha256,
        "issued_row_sha256": target.request.issued_row_sha256,
        "original_credential_session_sha256": target.request.credential_session_sha256,
        "original_main_uid": target.facts.main_uid,
        "original_origin": target.facts.origin,
        "exact_order_json": canonical(inner["exact_order"]).decode(),
        "hold_outcome_id": history.hold_outcome_id,
        "manifest_outcome_id": terminal[0],
        "assessment_sha256": sha(terminal[1].receipt_json),
        "assessment_json": terminal[1].receipt_json.decode(),
        "closure_body_sha256": schema.CURRENT_BODY_SHA256,
        "risk_hold_retained": False,
        "metrics_complete": False,
        "execution_authority": False,
        "order_retry_authority": False,
    }
    async with journal._transaction(binding) as parts:
        await _require_installed_schema(binding, parts[3])
        current = await journal._target_locked(
            binding, parts[1], parts[2], parts[3], parts[4], parts[-1]
        )
        journal.need(
            (current.current_state, current.current_state_revision) == ("uncertain", 3)
        )
        outcome = await journal._append(
            binding, parts, witness, kind="reconciliation", status="acknowledged"
        )
        stored = await parts[3].get(journal.Outcome, outcome, populate_existing=True)
        record = await parts[3].get(
            journal.Reservation, target.reservation_id, populate_existing=True
        )
        journal._source(binding)
        journal.need(
            await parts[3].scalar(
                text(
                    "SELECT public.qualification_native_protective_flat_data_v58(:rid,:at)"
                ),
                {"rid": target.reservation_id, "at": stored.recorded_at},
            )
            is True
        )
        record.state, record.state_revision, record.updated_at = (
            "reconciled_flat",
            4,
            stored.recorded_at,
        )
        parts[2]._journal(
            parts[3],
            record,
            "uncertain",
            schema.TRANSITION_REASON,
            stored.recorded_at,
            canonical(
                {
                    "version": schema.TERMINAL_VERSION,
                    "outcome_id": outcome,
                    "assessment_sha256": witness["assessment_sha256"],
                    "closure_body_sha256": schema.CURRENT_BODY_SHA256,
                    "execution_authority": False,
                    "order_retry_authority": False,
                }
            ).decode(),
        )
        await parts[3].flush()
        metadata = parts[-1]
    async with journal._transaction(binding) as parts:
        await _require_installed_schema(binding, parts[3])
        rows = await journal._outcomes(parts[3], target)
        matches = [row for row in rows if row.outcome_id == outcome]
        journal.need(
            len(matches) == 1
            and decode(matches[0].capture_json.encode(), 140000) == witness
            and parts[-1][8] != metadata[8]
        )
        record = await parts[3].get(
            journal.Reservation, target.reservation_id, populate_existing=True
        )
        journal.need((record.state, record.state_revision) == ("reconciled_flat", 4))
        _, observations = journal._read_manifest(rows, terminal[0])
        journal.need(
            facts.assess_original_protective_exit_v1(
                target.facts, history.original_observations, observations
            )
            == terminal[1]
        )
        journal.need(
            await parts[3].scalar(
                text(
                    "SELECT public.qualification_native_protective_flat_data_v58(:rid,:at)"
                ),
                {"rid": target.reservation_id, "at": matches[0].recorded_at},
            )
            is True
        )
    from app.trade_qualification.native_postsubmit_reconciliation_v1 import (
        NativeReconciliationResultV1,
    )

    result = NativeReconciliationResultV1(
        target.reservation_id,
        "original_oco_full_exit_flat",
        outcome,
        sha(terminal[1].receipt_json),
        False,
    )
    state["protective_flat_result"] = result
    return result


async def replay_owned_protective_exit_v1(binding):
    """Already resolved SQL tombstone plus NEW complete original-OCO readback."""
    state, _ = journal._source(binding)
    historical = state["verified_protective_flat_closure"]
    current = state["protective_durable_assessment"]
    journal.need(current[1].receipt_json == historical[1].receipt_json)
    async with journal._transaction(binding) as parts:
        await _require_installed_schema(binding, parts[3])
        record = await parts[3].get(
            journal.Reservation, state["reservation_id"], populate_existing=True
        )
        journal.need((record.state, record.state_revision) == ("reconciled_flat", 4))
        rows = await journal._outcomes(parts[3], state["target"])
        found = [row for row in rows if row.outcome_id == historical[0]]
        journal.need(len(found) == 1)
        journal.need(
            await parts[3].scalar(
                text(
                    "SELECT public.qualification_native_protective_flat_data_v58(:rid,:at)"
                ),
                {"rid": state["reservation_id"], "at": found[0].recorded_at},
            )
            is True
        )
    from app.trade_qualification.native_postsubmit_reconciliation_v1 import (
        NativeReconciliationResultV1,
    )

    result = NativeReconciliationResultV1(
        state["reservation_id"],
        "original_oco_full_exit_flat_previously_reconciled",
        historical[0],
        sha(current[1].receipt_json),
        False,
    )
    state["verified_normal_protected_recovery"] = (
        result,
        current[1],
        current[2],
        parts[-1],
    )
    return result
