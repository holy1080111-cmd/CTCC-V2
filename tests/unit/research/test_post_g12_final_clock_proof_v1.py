"""Pure replay checks for the diagnostic post-G12 final clock proof."""

from copy import deepcopy

import pytest

from app.domain.source_primitives import utc_from_ns
from app.trade_qualification import post_g12_final_clock_proof_v1 as proof

JOURNAL = "a" * 64
PUBLIC = {"utc_ns": 1_700_000_000_000_000_000, "monotonic_ns": 1_000_000_000}
CONSUMED = {"utc_ns": PUBLIC["utc_ns"] + 100_000_000, "monotonic_ns": 1_100_000_000}
FINAL = {"utc_ns": PUBLIC["utc_ns"] + 1_000_000_000, "monotonic_ns": 2_004_000_000}


def test_clock_proof_replays_both_monotonic_floors_and_never_grants_authority():
    record = proof.make_proof(PUBLIC, FINAL, JOURNAL, consume_clock=CONSUMED)
    expected = utc_from_ns(PUBLIC["utc_ns"] + 1_004_000_000)
    assert (
        proof.replay_proof(record, expected_public_journal_sha256=JOURNAL) == expected
    )
    assert record["effective_cutoff_at"] == expected.isoformat()
    assert record["execution_authority"] is False
    assert record["public_clock"] is not PUBLIC
    assert record["consume_clock"] is not CONSUMED


@pytest.mark.parametrize(
    "change",
    [
        {"effective_cutoff_at": utc_from_ns(FINAL["utc_ns"]).isoformat()},
        {"public_journal_sha256": "b" * 64},
        {"execution_authority": True},
        {"unexpected": False},
        {"consume_clock": {"utc_ns": FINAL["utc_ns"] + 1, "monotonic_ns": 0}},
        {
            "final_clock": {
                "utc_ns": FINAL["utc_ns"] - 10_000_000,
                "monotonic_ns": FINAL["monotonic_ns"],
            }
        },
    ],
)
def test_mutated_clock_proof_denies(change):
    record = proof.make_proof(PUBLIC, FINAL, JOURNAL, consume_clock=CONSUMED)
    changed = deepcopy(record)
    changed.update(change)
    with pytest.raises(
        proof.FinalClockProofError, match="post_g12_final_clock_proof_invalid"
    ):
        proof.replay_proof(changed, expected_public_journal_sha256=JOURNAL)


def test_proof_rejects_wrong_expected_journal_and_noncanonical_clock_types():
    record = proof.make_proof(PUBLIC, FINAL, JOURNAL)
    with pytest.raises(proof.FinalClockProofError):
        proof.replay_proof(record, expected_public_journal_sha256="b" * 64)
    forged = deepcopy(record)
    forged["public_clock"]["utc_ns"] = True
    with pytest.raises(proof.FinalClockProofError):
        proof.replay_proof(forged, expected_public_journal_sha256=JOURNAL)
