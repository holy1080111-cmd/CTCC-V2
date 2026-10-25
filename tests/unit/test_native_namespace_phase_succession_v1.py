"""Pure synthetic replay tests; fixture construction grants no native owner."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.database.repositories import controlled_writer_coverage as rows
from app.database.repositories.qualification_ledger import (
    _ConsumedEventJournalInspection,
)
from app.domain import prospective_event_cutover_v2 as records
from app.trade_qualification import demo_control as control
from app.trade_qualification import native_namespace_phase_v1 as phase
from tests.controlled_writer_coverage_fixtures import (
    coverage_observation_fixture,
    coverage_record_fixture,
    sha,
)
from tests.prospective_cutover_fixtures import empty_scan_document


def _series():
    at = datetime(2026, 10, 24, tzinfo=UTC)
    scope = control.ControlScope("demo", "730048")
    pins = control.ControlPins("1" * 64, "2" * 64, "3" * 64)
    previous, events, series = None, [], []
    last_active = None
    old_revoked = None
    for generation in range(1, 4):
        # Each phase occurs AFTER a complete five-minute candle wait. Source
        # and Control windows are fresh per phase; the old window stays old.
        now = at + timedelta(minutes=6 * (generation - 1))
        acquired = control.acquire(
            previous,
            scope=scope,
            owner_sha256=f"{generation + 3:x}" * 64,
            pins=pins,
            now=now,
        )
        for action, state in (
            ("acquire", acquired),
            (
                "estop",
                control.advance(
                    acquired,
                    owner_sha256=acquired.owner_sha256,
                    owner_epoch=acquired.owner_epoch,
                    expected_revision=acquired.revision,
                    action="estop",
                    command_id=f"{generation + 6:x}" * 64,
                    now=now + timedelta(microseconds=1),
                ),
            ),
        ):
            command = f"{state.revision:x}" * 64
            raw = control.canonical(
                {
                    "version": "ctcc.demo_control_event.v1",
                    "command_id": command,
                    "action": action,
                    "previous_sha256": None if not events else events[-1].event_sha256,
                    "state": control.document(state),
                }
            )
            events.append(
                control.ControlJournalSourceRowV1(
                    scope,
                    state.revision,
                    command,
                    action,
                    None if len(events) == 0 else events[-1].event_sha256,
                    sha(raw),
                    sha(control.canonical(control.document(state))),
                    raw,
                    state.updated_at,
                )
            )
        previous = state
        lineage = control.replay_control_journal_lineage_v1(
            tuple(events),
            scope=scope,
            target_state=state,
            expected_head_sha256=events[-1].event_sha256,
        )
        created = (
            state.updated_at.isoformat()
            if last_active is None
            else last_active.state_document["created_at"]
        )
        base = {
            "generation": generation,
            "epoch_id": f"{generation + 7:x}" * 32,
            "process_domain_nonce": f"{generation + 10:x}" * 32,
            "journal_sequence": 3 * generation - 2,
            "created_at": created,
            "source_custody_id": f"{generation + 8:x}" * 32,
            "source_bundle_sha256": f"{generation + 9:x}" * 64,
            "source_plan_sha256": f"{generation + 11:x}" * 64,
        }
        first = coverage_record_fixture(
            now=state.updated_at,
            control=state,
            control_event_sha256=events[-1].event_sha256,
            state_overrides=base,
            previous_sha256=None
            if old_revoked is None
            else old_revoked["event_sha256"],
        )
        active_overrides = dict(
            first["state"], state="active", journal_sequence=3 * generation - 1
        )
        active_record = coverage_record_fixture(
            now=state.updated_at,
            control=state,
            control_event_sha256=events[-1].event_sha256,
            state_overrides=active_overrides,
            previous_sha256=first["event_sha256"],
            audit_raw=first["audit_raw"],
        )
        active = coverage_observation_fixture(
            active_record["state_raw"],
            active_record["event_raw"],
            active_record["audit_raw"],
            prior_records=(first,),
            previous_terminal_anchor=old_revoked,
        )
        scan_at = now + timedelta(seconds=1)
        scan_doc = empty_scan_document(scope.account_id, scan_at)
        keys = frozenset({"a" * 64})
        scan_doc["event_count"] = 1
        scan_doc["event_keys_sha256"] = records.digest(records.canonical(sorted(keys)))
        scan = _ConsumedEventJournalInspection(
            keys, records.canonical(scan_doc), scan_at, scan_at
        )
        observed = SimpleNamespace(coverage=active, lineage=lineage, scan=scan)
        bound = {"active": active, "template": active.state_document}
        series.append((bound, observed))
        if generation < 3:
            revoke_at = now + timedelta(minutes=5)
            revoked = rows._revocation_document(
                active.state_document,
                now=revoke_at,
                reason="expired",
                observed={
                    "revision": state.revision,
                    "owner_epoch": state.owner_epoch,
                    "event_sha256": events[-1].event_sha256,
                    "state_sha256": sha(control.canonical(control.document(state))),
                    "emergency_stop": True,
                    "arm_request_id": None,
                },
            )
            old_revoked = coverage_record_fixture(
                now=revoke_at,
                control=state,
                control_event_sha256=events[-1].event_sha256,
                state_overrides=revoked,
                previous_sha256=active.journal_sha256,
                audit_raw=active.audit_json,
            )
        last_active = active
    old = series[0][1].coverage
    history = {"active": old, "event_keys": frozenset({"a" * 64})}
    return {"historical": history, "hops": ()}, series


def test_repeated_actual_join_covers_two_candle_waits_without_old_ttl_renewal():
    previous, series = _series()
    original_deadline = previous["historical"]["active"].state_document[
        "source_current_expires_at"
    ]
    first = phase._join(previous, *series[1])
    second = phase._join({**previous, "hops": first}, *series[2])
    assert len(first) == 1 and len(second) == 2
    assert second[0] is first[0]
    assert (
        previous["historical"]["active"].state_document["source_current_expires_at"]
        == original_deadline
    )
    assert (
        datetime.fromisoformat(original_deadline)
        < series[2][1].lineage.state.updated_at
    )


@pytest.mark.parametrize(
    "broken",
    [
        "generation_gap",
        "duplicate",
        "tombstone",
        "revocation",
        "control_prefix",
        "stop_gap",
    ],
)
def test_join_rejects_missing_succession_and_never_repairs_it(broken):
    previous, series = _series()
    state, observed = series[1]
    if broken == "generation_gap":
        state, observed = series[2]
    elif broken == "duplicate":
        state, observed = series[0]
    elif broken == "tombstone":
        observed = SimpleNamespace(**vars(observed))
        observed.scan = replace(observed.scan, consumed_event_keys=frozenset())
    elif broken == "revocation":
        snapshot = json.loads(observed.coverage.records_json)
        snapshot["previous_terminal_anchor"]["previous_sha256"] = "0" * 64
        observed = SimpleNamespace(**vars(observed))
        observed.coverage = replace(
            observed.coverage, records_json=rows.canonical(snapshot)
        )
        state = dict(state, active=observed.coverage)
    elif broken == "control_prefix":
        first = phase._join(previous, *series[1])
        first = (dict(first[0], control_rows=("corrupt_prefix",)),)
        previous = dict(previous, hops=first)
        state, observed = series[2]
    else:
        lineage = observed.lineage
        changed = list(lineage.source_rows)
        row = changed[2]
        document = json.loads(row.event_json)
        document["state"]["emergency_stop"] = False
        changed[2] = replace(row, event_json=control.canonical(document))
        # Pure input intentionally bypasses cryptographic replay to isolate the
        # join's additional stop-continuity guard; native capture always replays.
        observed = SimpleNamespace(**vars(observed))
        observed.lineage = replace(lineage, source_rows=tuple(changed))
    with pytest.raises(records.ProspectiveCutoverError):
        phase._join(previous, state, observed)
