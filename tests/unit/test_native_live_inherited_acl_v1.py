"""Source-pinned inherited DATA helpers; no new PUBLIC grant or DB admission."""

from copy import deepcopy

import pytest

from app.domain import native_live_inherited_acl_v1 as subject


def _rows():
    result = []
    for name, (
        args,
        returns,
        language,
        volatility,
        body,
        config,
    ) in subject.inherited_live_data_helper_specs_v1().items():
        result.append(
            {
                "name": name,
                "owner": 16384,
                "arguments": args,
                "returns": returns,
                "language": language,
                "volatility": volatility,
                "source": body,
                "config": config,
                "security_definer": False,
                "kind": "f",
                "parallel": "u",
                "strict": False,
                "leakproof": False,
                "returns_set": False,
                "defaults": 0,
                "support_zero": True,
                "acl": [[0, 16384, "EXECUTE", False], [16384, 16384, "EXECUTE", False]],
            }
        )
    return result


def test_allowlist_is_finite_exact_invoker_helpers_not_demo_or_live_authority():
    rows = _rows()
    names = subject.verify_inherited_live_helper_rows_v1(rows, owner_oid=16384)
    assert len(names) == 13 and "qualification_native_plain_v55" in names
    assert "qualification_native_protective_flat_data_v58" in names
    assert all(
        row["security_definer"] is False and row["returns"] != "trigger" for row in rows
    )
    assert len(subject.INHERITED_HELPER_SCOPE_SHA256) == 64


@pytest.mark.parametrize(
    "field,value",
    (
        ("source", "SELECT true"),
        ("security_definer", True),
        ("owner", 16385),
        ("arguments", ""),
        ("config", ["search_path=public"]),
        ("leakproof", True),
        ("returns_set", True),
        ("defaults", 1),
        ("support_zero", False),
        ("acl", [[0, 16384, "EXECUTE", True], [16384, 16384, "EXECUTE", False]]),
        (
            "acl",
            [
                [0, 16384, "EXECUTE", False],
                [16384, 16384, "EXECUTE", False],
                [20001, 16384, "EXECUTE", False],
            ],
        ),
    ),
)
def test_changed_body_flags_signature_owner_or_effective_public_acl_refuses(
    field, value
):
    rows = _rows()
    rows[0][field] = value
    with pytest.raises(
        ValueError, match="native_live_inherited_helper_catalog_refused"
    ):
        subject.verify_inherited_live_helper_rows_v1(rows, owner_oid=16384)


def test_unknown_missing_duplicate_or_extra_routine_never_becomes_allowlisted():
    rows = _rows()
    for changed in (
        rows[:-1],
        rows + [deepcopy(rows[0])],
        [{**row, "name": "unknown"} for row in rows],
    ):
        with pytest.raises(ValueError):
            subject.verify_inherited_live_helper_rows_v1(changed, owner_oid=16384)
