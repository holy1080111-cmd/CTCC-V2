import pytest

from scripts.current0055_runtime_grants import current55_core_grant_sql


def test_core_grants_keep_custody_raw_reader_and_writer_separate():
    grants = current55_core_grant_sql(
        writer="demo_writer", custody="demo_custody", frame_reader="demo_frame"
    )
    by_role = {
        role: {
            sql.removesuffix(f' TO "{role}"')
            for sql in grants
            if sql.endswith(f' TO "{role}"')
        }
        for role in ("demo_writer", "demo_custody", "demo_frame")
    }
    assert len(grants) == sum(map(len, by_role.values())) == 28
    common = {
        "GRANT USAGE ON SCHEMA public",
        "GRANT SELECT ON TABLE public.alembic_version",
    }
    assert by_role["demo_custody"] == common | {
        (
            "GRANT EXECUTE ON FUNCTION public.public_runtime_bundle_v1_append"
            "(text,text,text,bytea)"
        ),
        "GRANT EXECUTE ON FUNCTION public.public_runtime_bundle_v1_read(text)",
    }
    raw = by_role["demo_frame"] - common
    assert len(raw) == 2
    assert {sql.partition("(")[0] for sql in raw} == {
        "GRANT EXECUTE ON FUNCTION public.account_history_original_frame_v1_read",
        (
            "GRANT EXECUTE ON FUNCTION public."
            "account_history_original_frame_current55_v2_read"
        ),
    }
    writer = by_role["demo_writer"] - common
    assert {sql.partition("(")[0] for sql in writer if "FUNCTION" in sql} == {
        "GRANT EXECUTE ON FUNCTION public.account_history_partition_v1_read",
        "GRANT EXECUTE ON FUNCTION public.account_history_partition_current55_v2_append",
    }
    assert (
        "GRANT SELECT, INSERT, MAINTAIN ON TABLE "
        "public.qualification_reservation_transitions" in writer
    )
    assert (
        "GRANT USAGE ON SEQUENCE public.qualification_reservation_transitions_id_seq"
        in writer
    )
    assert all(
        word not in sql
        for sql in grants
        for word in ("GRANT ALL", "WITH GRANT OPTION", " ON ALL ", ";")
    )


@pytest.mark.parametrize(
    "role",
    (
        "demo_writer",
        'demo_frame"; GRANT ALL',
        "demo.frame",
        "public",
        "pg_database_owner",
        "r" * 64,
        "",
    ),
)
def test_collapsed_or_invalid_login_is_rejected_before_sql_generation(role):
    with pytest.raises(
        ValueError, match="^current0055_core_grant_scope_or_catalog_changed$"
    ):
        current55_core_grant_sql(
            writer="demo_writer", custody="demo_custody", frame_reader=role
        )
