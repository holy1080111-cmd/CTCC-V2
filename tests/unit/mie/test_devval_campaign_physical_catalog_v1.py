"""Pure synthetic controls; no PostgreSQL, TLS or private material is opened."""

import copy

import pytest

from scripts import devval_campaign_physical_catalog_v1 as catalog


def _document():
    bodies = catalog.expected_function_bodies()
    return {
        "schema": catalog.SCHEMA,
        "database": "ctcc_devval_campaign",
        "actor": "ctcc",
        "login": "ctcc",
        "version": "170011",
        "build": catalog.SERVER_BUILD,
        "tls": True,
        "durable": True,
        "has_alembic": False,
        "namespace": {"owner": "ctcc", "acl": []},
        "nonowner_acl": [list(row) for row in sorted(catalog.NONOWNER_ACL)],
        "classes": [
            {
                "name": name,
                "kind": "r" if name in catalog.TABLES else "i",
                "owner": "ctcc",
                "columns": [],
            }
            for name in sorted(catalog.TABLES | catalog.INDEXES)
        ],
        "functions": [
            {
                "name": name,
                "owner": "ctcc",
                "language": "plpgsql",
                "definer": True,
                "strict": False,
                "leakproof": False,
                "volatility": "v",
                "parallel": "u",
                "kind": "f",
                "support_zero": True,
                "config": ["search_path=pg_catalog, pg_temp"],
                "source_sha256": bodies[name],
            }
            for name in sorted(catalog.FUNCTIONS)
        ],
        "constraints": [
            {"name": name, "validated": True} for name in sorted(catalog.CONSTRAINTS)
        ],
        "indexes": [
            {"name": name, "valid": True, "ready": True}
            for name in sorted(catalog.INDEXES)
        ],
        "triggers": [
            {"name": name, "internal": False, "enabled": "O"}
            for name in sorted(catalog.USER_TRIGGERS)
        ]
        + [{"name": "internal", "internal": True, "enabled": "O"} for _ in range(8)],
        "types": [],
        "policies": [],
        "rules": [],
        "inheritance": [],
        "extensions": [],
        "event_triggers": [],
        "global_default_acl": [],
        "default_acl": [],
    }


def test_nine_function_bodies_come_from_both_exact_pinned_sql_files():
    bodies = catalog.expected_function_bodies()
    assert set(bodies) == catalog.FUNCTIONS
    assert len(set(bodies.values())) == 9
    assert catalog.EXPECTED_CATALOG_SHA256 == ""


def test_valid_synthetic_shape_remains_unsealed_then_test_pin_can_check_digest(
    monkeypatch,
):
    document = _document()
    with pytest.raises(catalog.CatalogRefusal, match="catalog_reference_unsealed"):
        catalog.validate_catalog_document(document)
    monkeypatch.setattr(
        catalog, "EXPECTED_CATALOG_SHA256", catalog.catalog_digest(document)
    )
    assert catalog.validate_catalog_document(document) == catalog.catalog_digest(
        document
    )


@pytest.mark.parametrize(
    "part,mutation",
    [
        ("classes", "missing"),
        ("functions", "body"),
        ("constraints", "unvalidated"),
        ("indexes", "invalid"),
        ("triggers", "extra"),
        ("classes", "rls"),
        ("policies", "extra"),
        ("event_triggers", "extra"),
        ("global_default_acl", "extra"),
        ("nonowner_acl", "public"),
        ("classes", "identity"),
    ],
)
def test_physical_drift_is_refused_before_reference_pin(monkeypatch, part, mutation):
    original = _document()
    monkeypatch.setattr(
        catalog, "EXPECTED_CATALOG_SHA256", catalog.catalog_digest(original)
    )
    document = copy.deepcopy(original)
    if mutation == "missing":
        document[part].pop()
    elif mutation == "rls":
        document[part][0]["rls"] = True
    elif mutation == "body":
        document[part][0]["source_sha256"] = "f" * 64
    elif mutation == "unvalidated":
        document[part][0]["validated"] = False
    elif mutation == "invalid":
        document[part][0]["ready"] = False
    elif mutation == "public":
        document[part].append(
            ["function", "_immutable", "PUBLIC", "EXECUTE", False, "ctcc"]
        )
    elif mutation == "identity":
        document[part][0]["columns"] = [
            ["id", "bigint", True, None, "a", "", "default", None]
        ]
    elif part == "triggers":
        document[part].append({"name": "unexpected", "internal": False, "enabled": "O"})
    else:
        document[part].append("unexpected")
    with pytest.raises(catalog.CatalogRefusal):
        catalog.validate_catalog_document(document)


def test_observer_refuses_before_any_connection_without_native_reference():
    class NoConnect:
        def connect(self):
            raise AssertionError("connection must not be opened")

    import asyncio

    with pytest.raises(catalog.CatalogRefusal, match="catalog_reference_unsealed"):
        asyncio.run(catalog.observe_catalog(NoConnect()))
