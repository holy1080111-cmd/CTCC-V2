"""DB0036/0037 append guards pin the current B3 policy and exact SQL repair."""

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


def test_db0037_repairs_only_membership_sha_regex_and_restores_0036_on_downgrade():
    previous = load_migration("0036")
    repair = load_migration("0037")
    broken = previous._guard(
        "ctcc.observed_account_execution_window.v2",
        policy_pin=True,
        require_same_epoch=True,
    )
    assert broken.count("^[a-f0-9]64$") == 1
    assert repair.revision == "0037" and repair.down_revision == "0036"
    assert repair._POLICY_V3_SHA256 == observed.POLICY_SHA256
    assert repair._RECEIPT_V2 == "ctcc.observed_account_execution_window.v2"
    assert repair._guard(repaired=True) == broken.replace(
        "^[a-f0-9]64$", "^[a-f0-9]{64}$"
    )
    assert repair._guard(repaired=False) == broken
