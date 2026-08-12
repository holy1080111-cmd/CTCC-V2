from __future__ import annotations

import ast
from pathlib import Path

from app.config.settings import Settings
from app.mie.contracts import DecisionCandidate, MieShadowTrace
from app.mie.features import MathematicalFeatureSnapshot


MIE_ROOT = Path(__file__).resolve().parents[3] / "app" / "mie"
APP_ROOT = MIE_ROOT.parent
FORBIDDEN_IMPORT_PREFIXES = (
    "app.demo_automation",
    "app.exchange",
    "app.execution",
    "app.okx_demo",
    "app.okx_live",
    "app.paper",
)
FORBIDDEN_RELATIVE_ROOTS = {
    prefix.removeprefix("app.").split(".", 1)[0]
    for prefix in FORBIDDEN_IMPORT_PREFIXES
}


def imported_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if not isinstance(node, ast.ImportFrom):
        return ()

    module = node.module or ""
    names = ([module] if module else []) + [
        f"{module}.{alias.name}" if module else alias.name
        for alias in node.names
    ]
    if node.level:
        names.extend(f"relative:{name}" for name in tuple(names))
    return tuple(names)


def test_mie_has_no_execution_side_imports() -> None:
    violations: list[str] = []
    for path in sorted(MIE_ROOT.rglob("*.py")):
        tree = ast.parse(
            path.read_text(encoding="utf-8-sig"), filename=str(path)
        )
        for node in ast.walk(tree):
            for name in imported_names(node):
                relative_root = name.removeprefix("relative:").split(
                    ".", 1
                )[0]
                if name.startswith(FORBIDDEN_IMPORT_PREFIXES) or (
                    name.startswith("relative:")
                    and relative_root in FORBIDDEN_RELATIVE_ROOTS
                ):
                    violations.append(
                        f"{path.relative_to(MIE_ROOT)}:{node.lineno}:{name}"
                    )

    assert violations == []


def test_mie_has_no_external_runtime_consumers() -> None:
    violations: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if path.is_relative_to(MIE_ROOT):
            continue
        tree = ast.parse(
            path.read_text(encoding="utf-8-sig"), filename=str(path)
        )
        for node in ast.walk(tree):
            for name in imported_names(node):
                if (
                    name == "app.mie"
                    or name.startswith("app.mie.")
                    or name == "relative:mie"
                    or name.startswith("relative:mie.")
                ):
                    violations.append(
                        f"{path.relative_to(APP_ROOT)}:{node.lineno}:{name}"
                    )

    assert violations == []


def test_mie_decision_and_trace_have_no_order_geometry() -> None:
    forbidden_fields = {
        "order_id",
        "client_order_id",
        "quantity",
        "contracts",
        "leverage",
        "margin",
        "exchange_payload",
        "write_authority",
    }
    assert forbidden_fields.isdisjoint(DecisionCandidate.model_fields)
    assert forbidden_fields.isdisjoint(MieShadowTrace.model_fields)
    assert forbidden_fields.isdisjoint(MathematicalFeatureSnapshot.model_fields)


def test_mie_does_not_change_fail_safe_runtime_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.auto_trade is False
    assert settings.live_trading is False
    assert settings.paper_auto_execution is False
    assert settings.okx_live_allow_order_writes is False
    assert settings.okx_live_auto_execution is False
    assert settings.okx_demo_allow_order_writes is False
    assert settings.okx_demo_auto_execution is False
    assert settings.okx_demo_score_risk_enabled is False
    assert settings.okx_demo_soak_allow_execute is False
