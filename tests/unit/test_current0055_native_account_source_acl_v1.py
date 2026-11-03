import pytest

from scripts.current0055_native_account_source_acl_v1 import (
    current55_native_account_source_grant_sql_v1,
)


def test_native_account_source_delta_is_exact_and_only_for_the_writer():
    grants = current55_native_account_source_grant_sql_v1(
        writer="demo_writer", custody="demo_custody", frame_reader="demo_frame"
    )
    expected = {
        "demo_account_capture_events": "SELECT, INSERT",
        "demo_account_observation_batches": "SELECT, INSERT",
        "demo_account_observation_facts": "SELECT, INSERT",
        "demo_account_observation_coverage": "SELECT, INSERT",
        "demo_account_observation_findings": "SELECT, INSERT",
        "demo_automation_state": "SELECT, MAINTAIN",
        "okx_demo_sync_checkpoints": "SELECT, INSERT, UPDATE",
        "okx_demo_order_state": "SELECT, MAINTAIN",
        "okx_demo_position_state": "SELECT, MAINTAIN",
        "okx_demo_algo_order_state": "SELECT, MAINTAIN",
        "audit_logs": "SELECT, INSERT",
    }
    assert len(grants) == len(expected)
    assert set(grants) == {
        f'GRANT {privileges} ON TABLE public.{table} TO "demo_writer"'
        for table, privileges in expected.items()
    }


@pytest.mark.parametrize("writer", ("demo_custody", "demo_frame", 'writer";'))
def test_native_account_source_delta_rejects_collapsed_or_unsafe_writer(writer):
    with pytest.raises(
        ValueError, match="^current0055_core_grant_scope_or_catalog_changed$"
    ):
        current55_native_account_source_grant_sql_v1(
            writer=writer, custody="demo_custody", frame_reader="demo_frame"
        )
