"""Exact additive58 post-flat raw DATA journal branch; importing installs none.

The original55 trigger and all legacy clauses stay unchanged. The new branch
recognizes only an immutable original-OCO terminal transition and its exact
acknowledged witness; it cannot create or alter the transition or its proof.
"""

from hashlib import sha256

from app.domain import native_protective_hold_schema_v58 as hold
from app.domain import native_submission_reporting_schema_v55 as previous

FUNCTION = previous.FUNCTION
ANCESTOR_SQL = previous.CURRENT_SQL
ANCESTOR_BODY_SHA256 = previous.CURRENT_BODY_SHA256
_START = "             OR (NEW.status='uncertain' AND r.state <> 'uncertain' AND NOT COALESCE(("
_END = "\n             OR NEW.sequence <>"
_CLOSE = "),false))"
_ADDITIONAL = """
               r.state='reconciled_flat' AND r.state_revision=4
               AND NEW.observation_kind='reconciliation'
               AND r.request_json::jsonb->>'contract_version'='ctcc-reservation-request-native-v4'
               AND r.environment='demo'
               AND NEW.capture_json::jsonb->'execution_authority'='false'::jsonb
               AND NEW.capture_json::jsonb->'order_retry_authority'='false'::jsonb
               AND NEW.capture_json::jsonb->>'version' IN (
                 'ctcc.native_read_page_metadata.v1', 'ctcc.native_read_raw_part.v1',
                 'ctcc.native_read_manifest.v1')
               AND EXISTS (
                 SELECT 1 FROM qualification_reservation_transitions closed
                 JOIN qualification_submission_outcomes witness ON witness.reservation_id=closed.reservation_id
                   AND witness.outcome_id=closed.evidence_json::jsonb->>'outcome_id'
                 WHERE closed.reservation_id=r.reservation_id AND closed.state_revision=4
                   AND closed.from_state='uncertain' AND closed.to_state='reconciled_flat'
                   AND closed.reason_code='post_submit_native_original_oco_flat_v58'
                   AND closed.occurred_at=witness.recorded_at
                   AND NEW.recorded_at>=closed.occurred_at
                   AND NEW.observed_at>=closed.occurred_at
                   AND witness.observation_kind='reconciliation' AND witness.status='acknowledged'
                   AND witness.intent_sha256=NEW.intent_sha256
                   AND witness.outcome_id=NEW.capture_json::jsonb->>'recovery_closed_outcome_id'
                   AND witness.capture_json::jsonb->>'version'='ctcc.native_protective_flat_closure.v1'
                   AND witness.capture_json::jsonb->>'reservation_id'=r.reservation_id
                   AND witness.capture_json::jsonb->>'request_sha256'=r.request_sha256
                   AND witness.capture_json::jsonb->>'intent_sha256'=NEW.intent_sha256
                   AND witness.capture_json::jsonb->>'closure_body_sha256'='__TRIGGER58__'
                   AND witness.capture_json::jsonb->'risk_hold_retained'='false'::jsonb
                   AND witness.capture_json::jsonb->'execution_authority'='false'::jsonb
                   AND witness.capture_json::jsonb->'order_retry_authority'='false'::jsonb
               )
""".replace("__TRIGGER58__", hold.CURRENT_BODY_SHA256)


def _extended():
    previous.statements()
    if ANCESTOR_SQL.count(_START) != 1 or ANCESTOR_SQL.count(_END) != 1:
        raise ValueError("native_submission58_original_guard_changed") from None
    start = ANCESTOR_SQL.index(_START)
    end = ANCESTOR_SQL.index(_END, start)
    original = ANCESTOR_SQL[start:end]
    if not original.endswith(_CLOSE):
        raise ValueError("native_submission58_original_guard_changed") from None
    old_allow = original[len(_START) : -len(_CLOSE)]
    added = _START + "(" + old_allow + ") OR (" + _ADDITIONAL + ")" + _CLOSE
    return ANCESTOR_SQL[:start] + added + ANCESTOR_SQL[end:]


CURRENT_SQL = _extended()
CURRENT_BODY_SHA256 = sha256(CURRENT_SQL.split("$$")[1].encode()).hexdigest()


def statements():
    if (
        CURRENT_SQL != _extended()
        or sha256(CURRENT_SQL.split("$$")[1].encode()).hexdigest()
        != CURRENT_BODY_SHA256
    ):
        raise ValueError("native_submission58_original_guard_changed") from None
    return (CURRENT_SQL,)


def restore_statements():
    statements()
    return (ANCESTOR_SQL,)
