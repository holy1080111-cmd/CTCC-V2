"""Public ingestion is an explicit read-only source, not a research promotion path."""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[3] / "app"
SOURCE = APP / "public_market_source"


def names(node):
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return (module, *(f"{module}.{alias.name}" for alias in node.names))
    return ()


def nodes(path):
    return ast.walk(ast.parse(path.read_text(encoding="utf-8-sig")))


def test_public_source_imports_no_account_settings_strategy_or_execution_layer():
    for path in SOURCE.rglob("*.py"):
        for node in nodes(path):
            for name in names(node):
                if name == "app" or name.startswith("app."):
                    assert name.startswith("app.public_market_source.") or (
                        name == "app.trade_evidence.storage"
                        or name.startswith("app.trade_evidence.storage.")
                    ), (path.name, name)


def test_public_source_has_only_the_explicit_offline_mie_consumer():
    consumer = APP / "mie" / "validation" / "measured_public_replay.py"
    observed = set()
    for path in APP.rglob("*.py"):
        if path.is_relative_to(SOURCE):
            continue
        for node in nodes(path):
            for name in names(node):
                if name.startswith("app.public_market_source"):
                    assert path == consumer, (path, name)
                    assert name.startswith(
                        (
                            "app.public_market_source.public_market_receipts",
                            "app.public_market_source.public_receipt_storage",
                        )
                    ), name
                    observed.add(path)
    assert observed == {consumer}


def test_public_source_io_capabilities_stay_in_reviewed_owners():
    # Journal imports httpx solely to classify timeout exceptions. Clock process
    # and native API access stay in the measured clock adapter; HTTP stays in
    # acquisition. The existing passive research boundary is unchanged.
    allowed = {
        "public_attempt_journal.py": {"httpx"},
        "public_market_capture.py": {"httpx", "httpcore", "ssl"},
        "public_clock.py": {"subprocess", "ctypes"},
    }
    controlled = {
        "httpx",
        "httpcore",
        "requests",
        "socket",
        "subprocess",
        "urllib",
        "ctypes",
        "ssl",
        "importlib",
    }
    literals = set()
    for path in SOURCE.rglob("*.py"):
        for node in nodes(path):
            for name in names(node):
                root = name.split(".", 1)[0]
                if root in controlled:
                    assert root in allowed.get(path.name, set()), (path.name, root)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.add(node.value)
    assert "GET" in literals
    assert not {"POST", "PUT", "PATCH", "DELETE"}.intersection(literals)


def test_public_source_package_init_has_no_import_side_effects():
    body = ast.parse((SOURCE / "__init__.py").read_text(encoding="utf-8")).body
    assert len(body) == 1
    assert isinstance(body[0], ast.Expr)
    assert isinstance(body[0].value, ast.Constant)
    assert isinstance(body[0].value.value, str)
