"""Synthetic shape controls only; these never seal a native catalog."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from scripts import current0056_fitted_delta_catalog_v1 as catalog

OWNER = "ctcc"
DEMO = ("ctcc_native_writer", "ctcc_native_custody", "ctcc_native_frame")
FITTED = {
    key: "ctcc_fitted_" + key
    for key in ("publisher", "observer", "reader", "evaluator")
}
ROLES = {"owner": OWNER, "demo_roles": DEMO, "fitted_roles": FITTED}


def synthetic_catalog() -> dict:
    functions = []
    for signature in sorted(catalog.INTERNAL | catalog.APIS.keys()):
        name = signature.split("(", 1)[0]
        functions.append(
            {
                "name": name,
                "signature": "public." + signature,
                "owner": OWNER,
                "language": (
                    "sql"
                    if name in {"gate3_fitted_hash", "gate3_fitted_json_hash"}
                    else "plpgsql"
                ),
                "kind": "f",
                "config": ["search_path=pg_catalog, pg_temp"],
                "definer": name
                in {
                    "gate3_fitted_role",
                    "gate3_fitted_commit_guard",
                    "gate3_fitted_candidate_claim_guard",
                    "gate3_fitted_binding_insert_guard",
                    "gate3_fitted_ack_insert_guard",
                    "gate3_fitted_evaluation_insert_guard",
                }
                or signature in catalog.APIS,
                "acl": ["ctcc=X/ctcc"],
                "definition_sha256": "a" * 64,
                "source_sha256": "b" * 64,
            }
        )
    grants = [
        ["public." + signature, FITTED[role], "EXECUTE", False, OWNER]
        for signature, roles in catalog.APIS.items()
        for role in roles
    ]
    triggers = []
    for name in sorted(catalog.TRIGGERS):
        if name == "gate3_fitted_candidate_claim_guard":
            table = "gate3_capture_schedule_key_claims"
        else:
            kind = name.removeprefix("gate3_fitted_").split("_", 1)[0]
            table = {
                "binding": "gate3_fitted_candidate_bindings",
                "ack": "gate3_fitted_candidate_acks",
                "evaluation": "gate3_fitted_candidate_evaluations",
            }[kind]
        suffix = (
            name.removeprefix("gate3_fitted_" + kind + "_")
            if (name != "gate3_fitted_candidate_claim_guard")
            else "claim_guard"
        )
        function = (
            "gate3_fitted_candidate_claim_guard"
            if suffix == "claim_guard"
            else "gate3_fitted_" + kind + "_insert_guard"
            if suffix == "insert_guard"
            else "gate3_fitted_commit_guard"
            if suffix == "commit_guard"
            else "gate3_fitted_immutable"
        )
        triggers.append(
            {
                "name": name,
                "table": table,
                "function": function,
                "internal": False,
                "enabled": "A",
                "deferrable": suffix == "commit_guard",
                "deferred": suffix == "commit_guard",
            }
        )
    return {
        "schema": catalog.SCHEMA,
        "head": "0056",
        "server_version": "170011",
        "server_build": "PostgreSQL 17.11 synthetic unit fixture",
        "actor": OWNER,
        "login": OWNER,
        "search_path": "pg_catalog, pg_temp",
        "tls": True,
        "read_only": True,
        "repeatable": True,
        "durable": True,
        "tables": [
            {
                "name": name,
                "kind": "r",
                "owner": OWNER,
                "persistence": "p",
                "rls": False,
                "force_rls": False,
                "columns": [],
                "acl": ["ctcc=arwdDxt/ctcc"],
            }
            for name in sorted(catalog.TABLES)
        ],
        "functions": functions,
        "function_grants": grants,
        "table_grants": [],
        "triggers": triggers,
        "constraints": [
            {"name": name, "validated": True} for name in sorted(catalog.CONSTRAINTS)
        ],
        "indexes": [
            {"name": name, "valid": True, "ready": True}
            for name in sorted(catalog.INDEXES)
        ],
        "policies": [],
        "rules": [],
        "inheritance": [],
        "event_triggers": [],
        "global_default_acl": [],
        "public_default_acl": [],
        "roles": [
            [name, True, False, False, False, False, False]
            for name in sorted({OWNER, *DEMO, *FITTED.values()})
        ],
        "role_create": [
            [name, False, False] for name in sorted({OWNER, *DEMO, *FITTED.values()})
        ],
        "role_edges": [],
        "original_write_access": [],
    }


class FittedDeltaInertTests(unittest.TestCase):
    def test_exact_migration_leaf_pinned(self):
        root = Path(__file__).resolve().parents[3]
        source = root
        catalog.pinned_migration(source)

    def test_query_is_fixed_read_only_select(self):
        sql = catalog.CATALOG_SQL.strip()
        self.assertTrue(sql.startswith("SELECT "))
        self.assertNotIn(";", sql)
        self.assertNotIn("GRANT ", sql)
        self.assertNotIn("UPDATE ", sql)

    def test_two_independent_synthetic_documents_match_but_cannot_seal(self):
        first = synthetic_catalog()
        second = copy.deepcopy(first)
        self.assertEqual(
            catalog.inspect_catalog_document(first, **ROLES),
            catalog.inspect_catalog_document(second, **ROLES),
        )
        with self.assertRaisesRegex(
            catalog.CatalogRefusal, "fitted_catalog_native_reference_unsealed"
        ):
            catalog.require_native_reference(first, **ROLES)

    def test_missing_historical_schedule_trigger_denied(self):
        changed = synthetic_catalog()
        changed["triggers"] = [
            row
            for row in changed["triggers"]
            if row["name"] != "gate3_fitted_candidate_claim_guard"
        ]
        with self.assertRaisesRegex(
            catalog.CatalogRefusal, "fitted_catalog_trigger_changed"
        ):
            catalog.inspect_catalog_document(changed, **ROLES)

    def test_wrong_head_and_extra_acl_denied(self):
        changed = synthetic_catalog()
        changed["head"] = "0055"
        with self.assertRaisesRegex(
            catalog.CatalogRefusal, "fitted_catalog_identity_changed"
        ):
            catalog.inspect_catalog_document(changed, **ROLES)
        changed = synthetic_catalog()
        changed["function_grants"].append(
            [
                "public.gate3_fitted_binding_read(text)",
                DEMO[0],
                "EXECUTE",
                False,
                OWNER,
            ]
        )
        with self.assertRaisesRegex(
            catalog.CatalogRefusal, "fitted_catalog_function_acl_changed"
        ):
            catalog.inspect_catalog_document(changed, **ROLES)

    def test_cross_role_membership_and_overlap_denied(self):
        changed = synthetic_catalog()
        changed["role_edges"].append([DEMO[0], FITTED["reader"]])
        with self.assertRaisesRegex(
            catalog.CatalogRefusal, "fitted_catalog_role_membership"
        ):
            catalog.inspect_catalog_document(changed, **ROLES)
        changed = synthetic_catalog()
        roles = dict(ROLES)
        roles["fitted_roles"] = FITTED | {"reader": DEMO[0]}
        with self.assertRaisesRegex(catalog.CatalogRefusal, "fitted_role_overlap"):
            catalog.inspect_catalog_document(changed, **roles)
        changed = synthetic_catalog()
        changed["original_write_access"].append(
            [
                FITTED["reader"],
                "public.gate3_prereg_seal_append(jsonb)",
            ]
        )
        with self.assertRaisesRegex(
            catalog.CatalogRefusal, "fitted_catalog_original_write_overlap"
        ):
            catalog.inspect_catalog_document(changed, **ROLES)
        changed = synthetic_catalog()
        next(row for row in changed["role_create"] if row[0] == FITTED["reader"])[2] = (
            True
        )
        with self.assertRaisesRegex(
            catalog.CatalogRefusal, "fitted_catalog_role_create"
        ):
            catalog.inspect_catalog_document(changed, **ROLES)

    def test_definition_change_changes_diagnostic_digest(self):
        original = synthetic_catalog()
        changed = copy.deepcopy(original)
        changed["functions"][0]["source_sha256"] = "c" * 64
        self.assertNotEqual(
            catalog.inspect_catalog_document(original, **ROLES),
            catalog.inspect_catalog_document(changed, **ROLES),
        )

    def test_unprovisioned_mode_allows_no_fitted_grants_only(self):
        original = synthetic_catalog()
        original["function_grants"] = []
        original["roles"] = [
            row for row in original["roles"] if row[0] not in FITTED.values()
        ]
        roles = {"owner": OWNER, "demo_roles": DEMO, "fitted_roles": None}
        self.assertEqual(len(catalog.inspect_catalog_document(original, **roles)), 64)
        original["function_grants"].append(
            [
                "public.gate3_fitted_binding_read(text)",
                DEMO[0],
                "EXECUTE",
                False,
                OWNER,
            ]
        )
        with self.assertRaisesRegex(
            catalog.CatalogRefusal, "fitted_catalog_function_acl_changed"
        ):
            catalog.inspect_catalog_document(original, **roles)


class InertRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_reference_refuses_before_connection_call(self):
        class NoConnection:
            async def fetchval(self, _sql):
                raise AssertionError("database call must not occur")

        with self.assertRaisesRegex(
            catalog.CatalogRefusal, "fitted_catalog_native_reference_unsealed"
        ):
            await catalog.observe_catalog(
                NoConnection(), source=Path("unused"), **ROLES
            )


if __name__ == "__main__":
    unittest.main()
