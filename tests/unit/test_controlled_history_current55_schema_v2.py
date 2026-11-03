"""Pure predicates/derivations only; synthetic catalog DATA is not PG admission."""

import copy

import pytest

from app.domain import controlled_history_current55_schema_v2 as schema


def _catalog(*, reader=False):
    owner, writer, raw = 101, 201, 301
    actor = raw if reader else writer
    rows = []
    for ordinal, (name, arguments, returns, body) in enumerate(
        (*schema.SPECS, *schema.ORIGINAL_SPECS)
    ):
        grants = {owner}
        if name in (schema.FRAME, schema.ORIGINALS[2]):
            grants.add(raw)
        elif name == schema.APPEND:
            grants.add(writer)
        rows.append(
            {
                "name": name,
                "oid": 1001 + ordinal,
                "namespace": "public",
                "namespace_oid": 2200,
                "owner_oid": owner,
                "arguments": ", ".join(
                    part.split(" ", 1)[1] for part in arguments.split(",")
                ),
                "identity_arguments": arguments.replace(",", ", "),
                "returns": returns,
                "language": "plpgsql",
                "source": body,
                "definition": "SYNTHETIC_NON_NATIVE_DEFINITION_" + name,
                "security_definer": True,
                "volatility": "v",
                "config": ["search_path=pg_catalog, public, pg_temp"],
                "kind": "f",
                "parallel": "u",
                "strict": False,
                "leakproof": False,
                "can_execute": actor in grants,
                "acl_valid": True,
                "acl": [
                    [grantee, owner, "EXECUTE", False] for grantee in sorted(grants)
                ],
            }
        )
    roles = [
        {
            "oid": oid,
            "name": name,
            "login": True,
            "superuser": False,
            "createdb": False,
            "createrole": False,
            "replication": False,
            "bypassrls": False,
            "membership": False,
            "create": False,
        }
        for oid, name in ((writer, "synthetic_writer"), (raw, "synthetic_raw"))
    ]
    return {
        "schema": schema.SCHEMA,
        "head": "0055",
        "database": "synthetic_data_only",
        "actor": actor,
        "current_user": "synthetic_actor",
        "session_user": "synthetic_actor",
        "role_setting": "none",
        "functions": rows,
        "roles": roles,
    }


def _observe(value, *, reader=False):
    return schema.observe_catalog(
        value,
        owner=101,
        actor=301 if reader else 201,
        owner_mode=False,
        reader_role_oid=301 if reader else None,
    )


def test_synthetic_writer_and_raw_metadata_have_same_inert_shared_result():
    writer, reader = _observe(_catalog()), _observe(_catalog(reader=True), reader=True)
    assert writer == reader
    assert writer["writer_role_oid"] == 201 and writer["raw_role_oid"] == 301
    assert (
        writer["account_complete"] is False and writer["execution_authority"] is False
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "old_head",
        "actor_bool",
        "owner_float",
        "duplicate_oid",
        "source_drift",
        "wrong_args",
        "public_execute",
        "grant_option",
        "raw_append",
        "writer_frame",
        "reader_mismatch",
        "privileged_reader",
        "set_role",
        "old_frame_acl",
        "configuration",
    ),
)
def test_actual_predicate_rejects_finite_metadata_faults(mutation):
    value = copy.deepcopy(_catalog())
    by_name = {row["name"]: row for row in value["functions"]}
    row = by_name[schema.APPEND]
    if mutation == "old_head":
        value["head"] = "0054"
    elif mutation == "actor_bool":
        value["actor"] = True
    elif mutation == "owner_float":
        row["owner_oid"] = 101.0
    elif mutation == "duplicate_oid":
        value["functions"][1]["oid"] = value["functions"][0]["oid"]
    elif mutation == "source_drift":
        row["source"] += "\n-- changed"
    elif mutation == "wrong_args":
        row["identity_arguments"] += ", injected text"
    elif mutation == "public_execute":
        row["acl"].append([0, 101, "EXECUTE", False])
    elif mutation == "grant_option":
        row["acl"][0][3] = True
    elif mutation == "raw_append":
        row["acl"].append([301, 101, "EXECUTE", False])
    elif mutation == "writer_frame":
        by_name[schema.FRAME]["acl"].append([201, 101, "EXECUTE", False])
    elif mutation == "reader_mismatch":
        by_name[schema.FRAME]["acl"] = [[101, 101, "EXECUTE", False]]
    elif mutation == "privileged_reader":
        value["roles"][1]["bypassrls"] = True
    elif mutation == "set_role":
        value["role_setting"] = "synthetic_writer"
    elif mutation == "old_frame_acl":
        by_name[schema.ORIGINALS[2]]["acl"] = [[101, 101, "EXECUTE", False]]
    elif mutation == "configuration":
        row["config"] = ["search_path=public"]
    with pytest.raises(schema.ControlledHistoryCurrent55Error):
        _observe(value)


def test_only_finite_original_body_deltas_and_wire_are_selected():
    assert (
        schema.LOCK_BODY.replace(schema._ARMED_FENCE, schema._OLD_FENCE, 1)
        == schema.OLD_LOCK_BODY
    )
    assert (
        schema.APPEND_BODY.replace(
            "public." + schema.LOCK + "(", "public." + schema.ORIGINALS[0] + "(", 1
        )
        == schema.OLD_APPEND_BODY
    )
    assert "original_frame_head_changed" in schema.FRAME_BODY
    assert "original_frame_predecessor" in schema.FRAME_BODY
    assert "original_frame_gap" in schema.FRAME_BODY
    assert "account_history_partition_current55_v2_lock" in schema.FRAME_BODY
    assert schema.FUNCTIONS == (
        "account_history_partition_current55_v2_lock",
        "account_history_partition_current55_v2_append",
        "account_history_original_frame_current55_v2_read",
    )
    assert len(schema.statements()) == 6
    assert all("GRANT " not in statement for statement in schema.statements())
    assert all("REVOKE ALL" in statement for statement in schema.REVOKE_SQL)
    assert schema.DROP_SQL[0].startswith("DROP FUNCTION public." + schema.FRAME + "(")
