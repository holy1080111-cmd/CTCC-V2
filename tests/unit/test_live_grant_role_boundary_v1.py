"""SQL preparation rejects global/system recipients before any owner action."""

import pytest

from app.domain import native_live_financial_epoch_schema_v1 as financial
from app.domain import native_live_ledger_schema_v1 as ledger


@pytest.mark.parametrize(
    "helper",
    (
        ledger.live_ledger_grant_sql_v1,
        financial.live_financial_collector_grant_sql_v1,
    ),
)
@pytest.mark.parametrize(
    "name",
    (
        "public",
        "PUBLIC",
        "pg_read_all_data",
        "pg_write_all_data",
        "pg_monitor",
        "pg_signal_backend",
        "pg_database_owner",
        'ctcc_role"; GRANT ALL TO PUBLIC;--',
        "",
        None,
        True,
    ),
)
def test_global_system_or_malformed_recipients_never_receive_a_grant_plan(helper, name):
    with pytest.raises(ValueError):
        helper(name)


def test_valid_restricted_roles_keep_the_exact_previous_grant_scope():
    writer = ledger.live_ledger_grant_sql_v1("ctcc_live_writer")
    collector = financial.live_financial_collector_grant_sql_v1("ctcc_live_collector")
    assert len(writer) == 17 and len(collector) == 15
    assert all(' TO "ctcc_live_writer"' in sql for sql in writer)
    assert all(' TO "ctcc_live_collector"' in sql for sql in collector)
    assert not any(
        " UPDATE " in sql or " DELETE " in sql for sql in (*writer, *collector)
    )
    assert not any(
        " INSERT " in sql and "native_live_controls_v1" in sql for sql in writer
    )
    assert all(
        "native_live_financial_" in sql for sql in collector if " INSERT " in sql
    )
