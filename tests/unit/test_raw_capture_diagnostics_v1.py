"""Only redacted fixture diagnostics; no collector, network or guard changes."""

import json

import pytest

from tests.unit.raw_capture_diagnostics_v1 import retained_public_failure_stage_v1


def test_refusal_diagnostic_retains_stage_and_never_raw_or_unknown_message():
    private = "synthetic-private-must-never-appear"
    content = {
        "plan.json": b"{}",
        "summary.json": b'{"disposition":"rejected"}',
        "event-0000.json": json.dumps(
            {"kind": "request", "metadata": {"role": "quote", "query": private}}
        ).encode(),
        "event-0000.raw": private.encode(),
        "event-0001.json": json.dumps(
            {
                "kind": "request_failed",
                "metadata": {
                    "code": "source_or_transport_rejected",
                    "private": private,
                },
            }
        ).encode(),
        "event-0002.json": json.dumps(
            {"kind": "component_failed", "metadata": {"role": "quote", "code": private}}
        ).encode(),
    }
    result = retained_public_failure_stage_v1(content)
    assert result["journal_parse_complete"] is True
    assert result["request_role_counts"] == {"quote": 1}
    assert result["last_event_kind"] == "component_failed"
    assert result["failure_stages"][1] == {
        "kind": "component_failed",
        "role": "quote",
        "code": "unreported",
    }
    assert private not in json.dumps(result)
    assert result["execution_authority"] is False


@pytest.mark.parametrize(
    "content", [{}, {"event-0000.json": b"malformed"}, {"summary.json": b"[]"}, None]
)
def test_missing_or_malformed_failure_diagnostic_cannot_turn_refusal_into_pass(content):
    result = retained_public_failure_stage_v1(content)
    assert result["execution_authority"] is False
    assert "status" not in result and "passed" not in result
