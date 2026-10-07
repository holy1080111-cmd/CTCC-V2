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
    shared_domain = {
        "app.domain.native_clock",
        "app.domain.source_primitives",
    }
    for path in SOURCE.rglob("*.py"):
        for node in nodes(path):
            for name in names(node):
                if name == "app" or name.startswith("app."):
                    assert name.startswith("app.public_market_source.") or (
                        name in shared_domain
                        or any(
                            name.startswith(f"{prefix}.") for prefix in shared_domain
                        )
                        or name == "app.trade_evidence.storage"
                        or name.startswith("app.trade_evidence.storage.")
                    ), (path.name, name)


def test_public_source_has_only_explicit_reviewed_consumers():
    consumer = APP / "mie" / "validation" / "measured_public_replay.py"
    allowed = {
        consumer: ("public_market_receipts", "public_receipt_storage"),
        APP / "mie" / "validation" / "blind_window_capture.py": (
            "public_market_receipts",
            "public_receipt_storage",
        ),
        APP / "mie" / "validation" / "blind_window_dataset.py": (
            "public_market_capture",
            "public_market_receipts",
            "public_receipt_storage",
        ),
        # Pure future-window planning reuses the exact V1 minute-plan contract;
        # this consumer owns no market I/O or execution authority.
        APP / "mie" / "validation" / "prospective_capture_schedule.py": (
            "public_market_receipts",
        ),
        APP / "mie" / "validation" / "public_checkpoint_service.py": (
            "public_checkpoint_hook",
            "public_market_capture",
            "public_market_receipts",
            "public_receipt_storage",
        ),
        APP / "database" / "repositories" / "public_receipt_witness.py": (
            "public_market_receipts",
            "public_receipt_storage",
        ),
        APP / "trade_qualification" / "public_source_runtime.py": (
            "public_clock",
            "public_runtime_journal",
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
                    if path.name == "blind_window_dataset.py" and name.startswith(
                        "app.public_market_source.public_market_capture"
                    ):
                        assert name in {
                            "app.public_market_source.public_market_capture",
                            "app.public_market_source.public_market_capture.replay_public_capture",
                        }, name
                    assert any(
                        name == f"app.public_market_source.{module}"
                        or name.startswith(f"app.public_market_source.{module}.")
                        for module in allowed[path]
                    ), name
                    observed.add(path)
    assert observed == set(allowed)


def test_runtime_consumers_cannot_access_private_writes_or_execution_authority():
    for filename in (
        "public_source_runtime.py",
        "post_g12_public_runtime.py",
        "qualification_runtime.py",
    ):
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
    # Only passive market acquisition owns HTTP. Host clock APIs live in the
    # domain adapter, outside this market package's dependency direction.
    allowed = {
        "public_attempt_journal.py": {"httpx"},
        "public_market_capture.py": {"httpx", "httpcore", "ssl"},
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


def test_account_time_probe_has_one_fixed_unauthenticated_get_route():
    path = APP / "trade_qualification" / "account_time_probe.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    methods, requests, sends = set(), [], []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        ):
            methods.add(node.value)
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "Request":
            requests.append(node)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "send":
            sends.append(node)
        for name in names(node):
            assert not any(
                blocked in name
                for blocked in (
                    "app.public_market_source",
                    "credentials",
                    "private_rest",
                    "execution_authority",
                    "submission_intent",
                )
            ), name
    assert methods == {"GET"}
    assert len(requests) == len(sends) == 1
    request = requests[0]
    assert len(request.args) >= 2
    assert isinstance(request.args[0], ast.Constant) and request.args[0].value == "GET"
    assert isinstance(request.args[1], ast.BinOp)
    assert (
        isinstance(request.args[1].left, ast.Name)
        and request.args[1].left.id == "ORIGIN"
    )
    assert (
        isinstance(request.args[1].right, ast.Name)
        and request.args[1].right.id == "TIME_ENDPOINT"
    )
    keywords = {item.arg: item.value for item in sends[0].keywords}
    assert (
        isinstance(keywords.get("auth"), ast.Constant)
        and keywords["auth"].value is None
    )
    assert (
        isinstance(keywords.get("follow_redirects"), ast.Constant)
        and keywords["follow_redirects"].value is False
    )
    assert (
        isinstance(keywords.get("stream"), ast.Constant)
        and keywords["stream"].value is True
    )


def test_public_source_package_init_has_no_import_side_effects():
    body = ast.parse((SOURCE / "__init__.py").read_text(encoding="utf-8")).body
    assert len(body) == 1
    assert isinstance(body[0], ast.Expr)
    assert isinstance(body[0].value, ast.Constant)
    assert isinstance(body[0].value.value, str)
