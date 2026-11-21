"""Closed source trace of the existing local-analysis HTTP and Paper SQL paths.

No application/router/service module is imported and no database is opened.
The result documents least-privilege requirements, not permission or readiness.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = (
    (
        "/dashboard/local-analysis",
        "GET",
        "app/dashboard/router.py",
        "local_analysis",
        "static_html",
    ),
    ("/liveness", "GET", "app/api/routers/system.py", "liveness", "settings_only"),
    (
        "/readiness",
        "GET",
        "app/api/routers/system.py",
        "readiness",
        "select1_and_redis_ping",
    ),
    ("/api/version", "GET", "app/api/routers/system.py", "version", "settings_only"),
    (
        "/api/capabilities",
        "GET",
        "app/api/routers/system.py",
        "capabilities",
        "settings_only",
    ),
    (
        "/api/market/supported-symbols",
        "GET",
        "app/api/routers/market.py",
        "supported_symbols",
        "static_symbols",
    ),
    (
        "/api/analysis/snapshot",
        "POST",
        "app/api/routers/analysis.py",
        "analyze_imported_snapshot",
        "offline_snapshot_no_sql",
    ),
    (
        "/api/paper/account",
        "GET",
        "app/api/routers/paper.py",
        "get_account",
        "paper_memory",
    ),
    (
        "/api/paper/state",
        "GET",
        "app/api/routers/paper.py",
        "get_state",
        "paper_memory",
    ),
    (
        "/api/paper/runtime/state",
        "GET",
        "app/api/routers/paper.py",
        "get_runtime_state",
        "paper_memory_under_lock",
    ),
    (
        "/api/paper/orders",
        "POST",
        "app/api/routers/paper.py",
        "submit_order",
        "paper_snapshot_persistence",
    ),
    (
        "/api/paper/orders/{order_id}",
        "GET",
        "app/api/routers/paper.py",
        "get_order",
        "paper_memory",
    ),
    (
        "/api/paper/orders/{order_id}/cancel",
        "POST",
        "app/api/routers/paper.py",
        "cancel_order",
        "paper_snapshot_persistence",
    ),
    (
        "/api/paper/ticks",
        "POST",
        "app/api/routers/paper.py",
        "process_tick",
        "conditional_paper_snapshot_persistence",
    ),
    (
        "/api/paper/positions/{position_id}",
        "GET",
        "app/api/routers/paper.py",
        "get_position",
        "paper_memory",
    ),
    (
        "/api/paper/positions/{position_id}/close",
        "POST",
        "app/api/routers/paper.py",
        "close_position",
        "paper_snapshot_persistence",
    ),
)
MODEL_TABLES = {
    "PaperAccountState": "paper_account_state",
    "PaperOrderState": "paper_order_state",
    "PaperPositionState": "paper_position_state",
    "RecoveryCheckpoint": "recovery_checkpoints",
    "AuditLog": "audit_logs",
    "SystemEvent": "system_events",
    "OrchestratorRunState": "orchestrator_run_state",
    "OrchestratorFingerprintState": "orchestrator_fingerprint_state",
}
REPOSITORY_METHODS = (
    "save_paper_state",
    "_save_paper_state",
    "load_paper_state",
    "mark_recovered",
    "counts",
)
SERVICE_METHODS = (
    "require_confirmed_state",
    "recover",
    "submit",
    "cancel",
    "tick",
    "close",
    "persist_now",
    "recovery_status",
    "get_order",
    "get_position",
    "state",
    "account",
    "runtime_state",
    "matches_runtime",
    "_mutate",
    "_apply_broker_mutation",
    "_persist_mutation",
    "_persist",
)
# These original modules bind the reviewed router, service and SQL factory.
# Their top-level construction has no function body to include below.
BINDING_MODULES = (
    "app/api/router.py",
    "app/database/session.py",
    "app/paper/engine.py",
    "app/paper/service.py",
)
# Exact source function digests make a new SQL/provider call require renewed
# route/CRUD review, rather than silently expanding the inferred privilege set.
FUNCTION_PINS = (
    (
        "app/analysis/service.py",
        "analyze_snapshot_at",
        "576353093b0af70c8ac4f8b63800b2418a680d865382245509ce3009008ecbf9",
    ),
    (
        "app/api/routers/analysis.py",
        "_analyze_imported_snapshot",
        "87cd580ba9b796034f1d7468e71c009131c968bf2f541a629726d226c0bc7b34",
    ),
    (
        "app/api/routers/analysis.py",
        "_read_imported_snapshot_body",
        "e6d64a5b01159e937a8cc2cd7dd2aae41f7dcdd4e5a417ec7c5b33f4b3fa7066",
    ),
    (
        "app/api/routers/analysis.py",
        "analyze_imported_snapshot",
        "3cff4b5dda437a1a009cc0eade82e0afe6c34fe03f3a1ac672a7779ff8af7ca4",
    ),
    (
        "app/api/routers/market.py",
        "supported_symbols",
        "f13adbb9fb9432721a60b53408e6c9335c1d33d4666579f6de38795418047c61",
    ),
    (
        "app/api/routers/paper.py",
        "_handle",
        "c060b8f07305d3bf2f2ee3874e924b1cffe15afda30753b10e7e7a89d5d8b7b8",
    ),
    (
        "app/api/routers/paper.py",
        "cancel_order",
        "fcd784135d40d6c23018ce03f1e2bf1c61e2cd258cb751e2e10f57ef98115472",
    ),
    (
        "app/api/routers/paper.py",
        "close_position",
        "60ea250a6870b2bff17730e5569031fd17143fba1788a79ce6d47837f91d4145",
    ),
    (
        "app/api/routers/paper.py",
        "get_account",
        "4c952d5c3a4fcb182b2141cc54c236c7c9f1f7a74a9077444d81da06276d344f",
    ),
    (
        "app/api/routers/paper.py",
        "get_order",
        "b1c5fb3b94a005323a10d736fc21b1b91800cab018ed5904595fcf2e54ff56b8",
    ),
    (
        "app/api/routers/paper.py",
        "get_position",
        "6ef453b7ba809d7fd35e61a2288f020bbe3a353d58e23f8b53b788ce8304bdfe",
    ),
    (
        "app/api/routers/paper.py",
        "get_runtime_state",
        "7208591949ee31a40d7bbceea255f870eb83c8d52ed5087144c811cd49c60bb6",
    ),
    (
        "app/api/routers/paper.py",
        "get_state",
        "856badc910eb7d851c37c7060b51a040a9f925b4032a76610c987c2d2ca4f4ff",
    ),
    (
        "app/api/routers/paper.py",
        "process_tick",
        "ee162664a4572267da311e45810a5d30f4968bcba593eb5282c0550c20a2f710",
    ),
    (
        "app/api/routers/paper.py",
        "require_expected_runtime",
        "7bf5cc888c1f0f8588f395e0fb47df424efe22bcd43c34ab3bff6db235e1a1c3",
    ),
    (
        "app/api/routers/paper.py",
        "submit_order",
        "525c6b6280df8962c3d6e9d73460400ceb7bf2a148afa83d3c45d72078d31b64",
    ),
    (
        "app/api/routers/system.py",
        "_readiness",
        "94ead1af25f1ad91ab1a992368c92ea5bdae0a88f83ea6505b1eb5c2437c55db",
    ),
    (
        "app/api/routers/system.py",
        "capabilities",
        "0867ffedace0f5907e5aed219d10251cd3b27177df8a6cd6fef118e2888b3648",
    ),
    (
        "app/api/routers/system.py",
        "liveness",
        "15ec6a402d2ed951ac2f6629157a910d86cfa45e85756556581c96ffce1d2be3",
    ),
    (
        "app/api/routers/system.py",
        "readiness",
        "7a112b80998e3cb2a83c08507117debfb91d42b880a2a3688546352a0ff27844",
    ),
    (
        "app/api/routers/system.py",
        "version",
        "25ad696568a1503d2423f54a5eec485c75721609deb0968ac0dea0fc2d757044",
    ),
    (
        "app/api/security.py",
        "require_ctcc_token",
        "996a7c10f5747c037f011ad6d0ceedc5afbd53fa84c83427e4fef804259ce195",
    ),
    (
        "app/dashboard/router.py",
        "local_analysis",
        "003c75e50e0f93e73194d6dd3acd2d9a4fd8976220433ec3cd5471575dd21ece",
    ),
    (
        "app/database/health.py",
        "check_database",
        "5502ca49180f4429e5bbc4d95f55ef203fc6df1796ff6f4aa1d68058bc796e83",
    ),
    (
        "app/database/repositories/persistence.py",
        "PersistenceRepository.__init__",
        "ddfc6aa5cc1e9d44c8e9b291f045115e43a667bffab2de14de3372a32233aa82",
    ),
    (
        "app/database/repositories/persistence.py",
        "PersistenceRepository._save_paper_state",
        "78017d30024db3aa035af01fe51a98ad28c101a740ba08004abd9238799fb7e9",
    ),
    (
        "app/database/repositories/persistence.py",
        "PersistenceRepository.counts",
        "467b7b22b831dec0162b3798590b9e42601d99bc874dfd91bd7318db58a02cf6",
    ),
    (
        "app/database/repositories/persistence.py",
        "PersistenceRepository.load_paper_state",
        "b9811be958f87253df67e800509c2b4050d736cde4d3fa0423ee156189af6835",
    ),
    (
        "app/database/repositories/persistence.py",
        "PersistenceRepository.mark_recovered",
        "f5c316092c9cfb4934590a04bd4132c9b7d621dcf94ec7e8da9fbc82f04bbf7d",
    ),
    (
        "app/database/repositories/persistence.py",
        "PersistenceRepository.save_paper_state",
        "bd0c09a4ef112a9ce01584697aad7c7aee0f49e7c2bb485f976d1e28718552bb",
    ),
    (
        "app/database/repositories/persistence.py",
        "_canonical",
        "5380e8eec6595beeaa755cbb8d9f5a3ebdf339a0da6edcca98665c4938d310e2",
    ),
    (
        "app/database/repositories/persistence.py",
        "durable_state_payload",
        "3e4f2bd3d32050d6c610384eafb20dde1f7f30ff70ab701b39710eeb4af470ca",
    ),
    (
        "app/database/repositories/persistence.py",
        "state_checksum",
        "90f41f1a4092b8337f29564906c8e9a0b1273fb8d13086da097da0d5dd84e873",
    ),
    (
        "app/main.py",
        "_local_analysis_router",
        "c7cc4750fb32bdc50081b2730b9f6d59ab213d49e2b26d77432df052afda2f11",
    ),
    (
        "app/main.py",
        "_local_dashboard_redirect",
        "fa435863841ae498e5bc98abb1350378d154ea8be701986feea07ff1c1c60b87",
    ),
    (
        "app/main.py",
        "lifespan",
        "35db440a2daff4ca2eab1b601b3f0094953a40eba9e64d6adc4147d3a1df7eab",
    ),
    (
        "app/monitoring/redis_health.py",
        "check_redis",
        "4b00e7a7c1c8a6b4e2a8ca6234922ce62bf9beeaa0402b53e34343de9df82635",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService.__init__",
        "be3106b53f141cc6e808ed79954b4a14efeb3ecc20a94ed866f1bffcf601df82",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService._apply_broker_mutation",
        "52c4cb0842cdb7536709155aa74f043ad0e33799c57faeb37a80dda5b5f2ddae",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService._mutate",
        "1dde7efd612ff4982ac97f445304a0219399d41c54d3e979e5ad3fde557806dd",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService._persist",
        "26410f95d0f93480a16ddf095a02bb41571807ca5b81ec491805ab90eab9577b",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService._persist_mutation",
        "12178c15eb5034de03bd806a20db696545bc2337b188b8aa088c5bdc6686e185",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService.account",
        "51f90ac91967defdb13752a2e3e77d5a44159473d676ed3b8e3cd068f93249e3",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService.cancel",
        "2efff0399077b48d8363cebfbfcebf97e5ccc1a93bb02ff5e7a4e9cafcc58669",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService.close",
        "3852e45b2d64ab7c06a7b7945413e3409de68e2a306db23c0dd1f794df94ffba",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService.get_order",
        "a409dc8f023a28d55f6778a2f38bc8e7daca47dc5faf8764e2021e7f63997bf5",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService.get_position",
        "185ba7e1f22e1c7dfacbac8b0c5cd11ec55df4299c5ef4cdba868296b650440a",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService.matches_runtime",
        "f1b9e81c88904f4a2ea9756c2a7a9aaf5b2417a3edc4ab6bf0a8e38eb7e03780",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService.persist_now",
        "d941bb237ab8fcaf0389a09ad8aff9abf1f4c9882e4c7591ec18a5b063a2d469",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService.recover",
        "411eefc28b7dd685da1967cbe56147db7b41f684c6ec94285c54f7cd853721ca",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService.recovery_status",
        "434ada8611461772a94c9bc9f64f55ce29e5bd9e00168e0285d3a84ad0c88ec7",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService.require_confirmed_state",
        "79eb94e122b97a9eb67a909277108585070d88b67bbb17adcb6a960a551ad74c",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService.runtime_state",
        "5ade2fa941208acc6ac502d26a3a8b9c120136a0fe31cd359cbccc4e3d37610f",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService.state",
        "5a8773ef7523795e72e0e63189717af32fece9ae89bc2a2eb0d7502fc9a189e3",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService.submit",
        "924641fdb675468a2b134420584d7cee61b77d4db3ed050104d03e63541c7dd9",
    ),
    (
        "app/paper/execution_service.py",
        "PaperExecutionService.tick",
        "23e368bc381e08eb417fd6fef18c9955d6597c276762169b69fc2cf8670d6501",
    ),
)
MODULE_PINS = (
    (
        "app/api/router.py",
        "d854a476419de3d37828c4b60530012c9d62cacff8a68ce123a90a7b35953b9c",
    ),
    (
        "app/database/session.py",
        "d3f207a43dcb5620dd06952751d93e636e0b0500d3e93142b40b2d196b6ac54d",
    ),
    (
        "app/paper/engine.py",
        "78607f7b1d2cda42542f68e88e9ec9a3fa964b5d4fad98f8bc7fdbf0aefba185",
    ),
    (
        "app/paper/service.py",
        "b66d154a907a66dfe5dbd1f58aabcdc9b518c610a53eeaaefb9ffba3f12df92c",
    ),
)


def _need(value):
    if value is not True:
        raise ValueError("paper58_local_route_scope_changed") from None


def _tree(path):
    file = ROOT / path
    _need(file.is_file() and not file.is_symlink())
    return ast.parse(file.read_bytes(), filename=path)


def _function(path, name):
    nodes = _tree(path).body
    parts = name.split(".")
    if len(parts) == 2:
        nodes = next(
            node.body
            for node in nodes
            if isinstance(node, ast.ClassDef) and node.name == parts[0]
        )
    matches = [
        node
        for node in nodes
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == parts[-1]
    ]
    _need(len(matches) == 1)
    return matches[0]


def reviewed_functions_v1():
    values = {(path, name) for _route, _method, path, name, _scope in ROUTES}
    values.update(
        ("app/paper/execution_service.py", "PaperExecutionService." + name)
        for name in SERVICE_METHODS
    )
    values.update(
        ("app/database/repositories/persistence.py", "PersistenceRepository." + name)
        for name in REPOSITORY_METHODS
    )
    values.update(
        (path, name)
        for path, name in (
            ("app/api/routers/analysis.py", "_read_imported_snapshot_body"),
            ("app/api/routers/analysis.py", "_analyze_imported_snapshot"),
            ("app/analysis/service.py", "analyze_snapshot_at"),
            ("app/api/routers/system.py", "_readiness"),
            ("app/database/health.py", "check_database"),
            ("app/monitoring/redis_health.py", "check_redis"),
            ("app/api/security.py", "require_ctcc_token"),
            ("app/api/routers/paper.py", "require_expected_runtime"),
            ("app/api/routers/paper.py", "_handle"),
            ("app/database/repositories/persistence.py", "_canonical"),
            ("app/database/repositories/persistence.py", "durable_state_payload"),
            ("app/database/repositories/persistence.py", "state_checksum"),
            (
                "app/database/repositories/persistence.py",
                "PersistenceRepository.__init__",
            ),
            ("app/paper/execution_service.py", "PaperExecutionService.__init__"),
            ("app/main.py", "_local_dashboard_redirect"),
            ("app/main.py", "_local_analysis_router"),
            ("app/main.py", "lifespan"),
        )
    )
    result = []
    for path, name in sorted(values):
        # Preserve the function's actual imported bindings too. A new SQL or
        # provider import must not pass merely because the handler is unchanged.
        imports = [
            node
            for node in _tree(path).body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        definition = ast.Module(body=[*imports, _function(path, name)], type_ignores=[])
        result.append(
            (
                path,
                name,
                hashlib.sha256(
                    ast.dump(definition, include_attributes=False).encode()
                ).hexdigest(),
            )
        )
    return tuple(result)


def reviewed_bindings_v1():
    return tuple(
        (
            path,
            hashlib.sha256(
                ast.dump(_tree(path), include_attributes=False).encode()
            ).hexdigest(),
        )
        for path in BINDING_MODULES
    )


def _model(node):
    if isinstance(node, ast.Name) and node.id in MODEL_TABLES:
        return node.id
    if isinstance(node, ast.Attribute):
        return _model(node.value)
    return None


def repository_crud_v1():
    result = {}
    for method in REPOSITORY_METHODS:
        node = _function(
            "app/database/repositories/persistence.py",
            "PersistenceRepository." + method,
        )
        found, inserts = set(), set()
        for call in (item for item in ast.walk(node) if isinstance(item, ast.Call)):
            name = (
                call.func.id
                if isinstance(call.func, ast.Name)
                else call.func.attr
                if isinstance(call.func, ast.Attribute)
                else None
            )
            if name in {"select", "pg_insert", "delete"} and call.args:
                model = _model(call.args[0])
                if model is not None:
                    operation = {
                        "select": "SELECT",
                        "pg_insert": "INSERT",
                        "delete": "DELETE",
                    }[name]
                    found.add((MODEL_TABLES[model], operation))
                    if name == "pg_insert":
                        inserts.add(model)
            elif (
                name == "get"
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "session"
                and call.args
            ):
                model = _model(call.args[0])
                _need(model is not None)
                found.add((MODEL_TABLES[model], "SELECT"))
            elif (
                name == "add"
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "session"
                and call.args
            ):
                _need(isinstance(call.args[0], ast.Call))
                model = _model(call.args[0].func)
                _need(model is not None)
                found.update(
                    {(MODEL_TABLES[model], "INSERT"), (MODEL_TABLES[model], "SELECT")}
                )
        # Every source pg_insert here is an UPSERT. SELECT also covers the
        # conflict/current-value expressions and generated RETURNING fields.
        updates = any(
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Attribute)
            and item.func.attr == "on_conflict_do_update"
            for item in ast.walk(node)
        )
        for model in inserts:
            _need(updates)
            found.update(
                {(MODEL_TABLES[model], "UPDATE"), (MODEL_TABLES[model], "SELECT")}
            )
        result[method] = tuple(sorted(found))
    return result


def route_scope_v1():
    tree = _tree("app/main.py")
    actual = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_LOCAL_ANALYSIS_ROUTES"
            for target in node.targets
        )
    )
    _need(actual == {route: method for route, method, _path, _name, _scope in ROUTES})
    _need(reviewed_functions_v1() == FUNCTION_PINS)
    _need(reviewed_bindings_v1() == MODULE_PINS)
    for route, method, path, name, _scope in ROUTES:
        source, function = _tree(path), _function(path, name)
        prefixes = {}
        for node in source.body:
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "APIRouter"
            ):
                prefix = next(
                    (
                        ast.literal_eval(value.value)
                        for value in node.value.keywords
                        if value.arg == "prefix"
                    ),
                    "",
                )
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        prefixes[target.id] = prefix
        matches = [
            prefixes[decorator.func.value.id] + ast.literal_eval(decorator.args[0])
            for decorator in function.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and isinstance(decorator.func.value, ast.Name)
            and decorator.func.value.id in prefixes
            and decorator.func.attr.upper() == method
        ]
        _need(matches == [route])
    from scripts.paper58_source_coverage_v1 import RUNTIME_TABLE_PRIVILEGES

    crud = repository_crud_v1()
    required = {cell for cells in crud.values() for cell in cells}
    allowed = {
        (table, privilege)
        for table, privileges in RUNTIME_TABLE_PRIVILEGES
        for privilege in privileges
    }
    _need(required == allowed and len(required) == 20)
    return {
        "schema": "ctcc.paper58.local_route_crud_source.v1",
        "routes": ROUTES,
        "redirects": (
            ("/", "GET", "/dashboard/local-analysis"),
            ("/dashboard", "GET", "/dashboard/local-analysis"),
        ),
        "reviewed_functions": FUNCTION_PINS,
        "reviewed_bindings": MODULE_PINS,
        "repository_crud": crud,
        "readiness_sql": "SELECT 1",
        "redis_operation": "PING",
        "generic_readiness_is_reachability_only": True,
        "paper_readiness_requires_owned_recovery_context": True,
        "analysis_snapshot_sql_operations": (),
        "analysis_table_grants_required": (),
        "separate_analysis_actor_required_for_current_whitelist": False,
        "paper_writes": "original_service_snapshot_upsert_delete_and_audit",
        "paper_gets": "original_broker_memory_runtime_lock_and_unknown_commit_fence",
        "boot_and_shutdown": ("recover", "recovery_status", "persist_now"),
        "runtime_table_cells": 20,
        "marker_preflight_select_cells": 1,
        "total_table_cells": 21,
        "total_tables": 9,
        "native_trading_execution_authority": False,
        "startup_admitted": False,
    }
