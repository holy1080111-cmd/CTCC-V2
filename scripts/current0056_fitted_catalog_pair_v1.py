"""Inert comparison of two separately observed DB0056 fitted catalogs.

This is a pure diagnostic over externally retained measurements. A caller's
receipt cannot establish that a database was actually observed; no result from
this module is a native reference, startup admission, or trading authority.
"""

from __future__ import annotations

import hashlib
import json
import re

from scripts import current0056_fitted_delta_catalog_v1 as fitted

PG_IMAGE = (
    "postgres:17-alpine@sha256:"
    "f02121de6f74d30d8a94cd1d9584125e2178d7e6c377d8130112d4e52d867995"
)
PG_BUILD = (
    "PostgreSQL 17.11 on x86_64-pc-linux-musl, compiled by "
    "gcc (Alpine 15.2.0) 15.2.0, 64-bit"
)
_OTHER = re.compile(r"other_[0-9a-f]{64}\Z")
_TOKENS = (
    "owner",
    "demo_writer",
    "demo_custody",
    "demo_frame",
    "fitted_publisher",
    "fitted_observer",
    "fitted_reader",
    "fitted_evaluator",
)


class FittedPairRefusal(ValueError):
    pass


def _need(condition: bool, code: str) -> None:
    if condition is not True:
        raise FittedPairRefusal(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _acl(value: str, names: dict[str, str]) -> str:
    _need(
        type(value) is str and value.count("=") == value.count("/") == 1,
        "fitted_pair_acl",
    )
    grantee, rest = value.split("=", 1)
    privileges, grantor = rest.split("/", 1)
    _need(
        grantee in names
        and grantor in names
        and bool(re.fullmatch(r"[A-Za-z*]+", privileges, flags=re.ASCII)),
        "fitted_pair_acl",
    )
    return f"{names[grantee]}={privileges}/{names[grantor]}"


def _normalized(document: dict, names: dict[str, str]) -> dict:
    """Replace only catalog role fields, then compare every remaining byte."""
    result = json.loads(_canonical(document))
    for key in ("actor", "login"):
        result[key] = names[result[key]]
    for row in result["tables"]:
        row["owner"] = names[row["owner"]]
        if row["acl"] is not None:
            row["acl"] = sorted(_acl(item, names) for item in row["acl"])
    for row in result["functions"]:
        row["owner"] = names[row["owner"]]
        if row["acl"] is not None:
            row["acl"] = sorted(_acl(item, names) for item in row["acl"])
    for row in result["function_grants"]:
        row[1] = names[row[1]]
        row[4] = names[row[4]]
    result["function_grants"].sort(key=_canonical)
    for key in ("roles", "role_create"):
        for row in result[key]:
            if row[0] in names:
                row[0] = names[row[0]]
        result[key].sort(key=_canonical)
    for row in result["original_write_access"]:
        if row[0] in names:
            row[0] = names[row[0]]
    result["original_write_access"].sort(key=_canonical)
    return result


def _scrub_other_role(value: str) -> str:
    _need(type(value) is str and 0 < len(value) <= 256, "fitted_pair_other_role")
    if value in _TOKENS or _OTHER.fullmatch(value) is not None:
        return value
    return "other_" + _sha(value.encode("utf-8"))


def normalized_catalog_document(
    document: dict,
    *,
    owner: str,
    demo_roles: tuple[str, str, str],
    fitted_roles: dict[str, str],
) -> dict:
    """Validate raw physical shape and scrub all retained role-name fields."""
    fitted.inspect_catalog_document(
        document, owner=owner, demo_roles=demo_roles, fitted_roles=fitted_roles
    )
    names = dict(
        zip(
            (
                owner,
                *demo_roles,
                *(
                    fitted_roles[key]
                    for key in ("publisher", "observer", "reader", "evaluator")
                ),
            ),
            _TOKENS,
            strict=True,
        )
    )
    _need(len(names) == 8, "fitted_pair_roles")
    normalized = _normalized(document, names)
    for key in ("roles", "role_create"):
        for row in normalized[key]:
            row[0] = _scrub_other_role(row[0])
        normalized[key].sort(key=_canonical)
    for row in normalized["role_edges"]:
        row[0] = _scrub_other_role(row[0])
        row[1] = _scrub_other_role(row[1])
    normalized["role_edges"].sort(key=_canonical)
    for row in normalized["original_write_access"]:
        row[0] = _scrub_other_role(row[0])
    normalized["original_write_access"].sort(key=_canonical)
    _need(
        normalized["server_build"] == document["server_build"] == PG_BUILD
        and normalized["server_version"] == "170011",
        "fitted_pair_pg_runtime",
    )
    redacted = _canonical(normalized)
    _need(
        b"ctcc_" not in redacted
        and all(
            name.encode("utf-8") not in redacted
            for name in (*demo_roles, *fitted_roles.values())
        ),
        "fitted_pair_role_redaction",
    )
    return normalized


def verify_normalized_catalog_document(document: dict) -> str:
    """Rerun strict shape checks on retained role-neutral physical evidence."""
    _need(type(document) is dict, "fitted_pair_normalized_invalid")
    owner = "ctcc"
    demo = (
        "ctcc_pair_demo_writer",
        "ctcc_pair_demo_custody",
        "ctcc_pair_demo_frame",
    )
    roles = {
        key: "ctcc_pair_fitted_" + key
        for key in ("publisher", "observer", "reader", "evaluator")
    }
    reverse = dict(
        zip(
            _TOKENS,
            (owner, *demo, *(roles[key] for key in roles)),
            strict=True,
        )
    )
    try:
        reconstructed = _normalized(document, reverse)
        replayed = normalized_catalog_document(
            reconstructed, owner=owner, demo_roles=demo, fitted_roles=roles
        )
    except (KeyError, TypeError, IndexError, fitted.CatalogRefusal, FittedPairRefusal):
        raise FittedPairRefusal("fitted_pair_normalized_invalid") from None
    _need(replayed == document, "fitted_pair_normalized_changed")
    return _sha(_canonical(document))
