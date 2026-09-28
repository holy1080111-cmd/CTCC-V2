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


def test_public_source_has_only_explicit_reviewed_consumers():
    consumer = APP / "mie" / "validation" / "measured_public_replay.py"
    allowed = {
        consumer: ("public_market_receipts", "public_receipt_storage"),
        APP / "trade_qualification" / "public_source_runtime.py": (
            "public_clock",
            "public_market_receipts",
            "public_runtime_journal",
        ),
        APP / "trade_qualification" / "post_g12_public_runtime.py": (
            "public_clock",
            "public_market_receipts",
        ),
    }
    observed = set()
    for path in APP.rglob("*.py"):
        if path.is_relative_to(SOURCE):
            continue
        for node in nodes(path):
            for name in names(node):
                if name.startswith("app.public_market_source"):
                    assert path in allowed, (path, name)
                    assert any(
                        name == f"app.public_market_source.{module}"
                        or name.startswith(f"app.public_market_source.{module}.")
                        for module in allowed[path]
                    ), name
                    observed.add(path)
    assert observed == set(allowed)


def test_runtime_consumers_cannot_access_private_writes_or_execution_authority():
    for filename in ("public_source_runtime.py", "post_g12_public_runtime.py"):
        for node in nodes(APP / "trade_qualification" / filename):
            for name in names(node):
                assert not any(
                    part in name
                    for part in (
                        "execution_authority",
                        "private_rest",
                        "credentials",
                        "submission_intent",
                        "qualification_ledger",
                        "account_runtime",
                        "dispatch_ownership",
                    )
                ), (filename, name)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in {"POST", "PUT", "PATCH", "DELETE"}


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
