"""The DB0036 append guard must pin the policy emitted by current B3 replay."""

from app.trade_qualification import account_observation_index as observed
from tests.durable_migration_fixtures import load_migration


def test_db0036_append_policy_matches_current_b3_receipt():
    migration = load_migration("0036")
    assert migration._POLICY_V3_SHA256 == observed.POLICY_SHA256
    sql = migration._guard(
        "ctcc.observed_account_execution_window.v2",
        policy_pin=True,
        require_same_epoch=True,
    )
    assert "ctcc.observed_account_execution_window.v2" in sql
    assert observed.POLICY_SHA256 in sql
    assert "prior.document_json::jsonb->>'policy_sha256'" in sql
