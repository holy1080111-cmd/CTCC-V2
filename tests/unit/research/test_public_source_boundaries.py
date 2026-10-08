"""Public ingestion is an explicit read-only source, not a research promotion path."""

import ast
import sys
from pathlib import Path

import pytest

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
    post_publication_consumer = (
        APP / "mie" / "validation" / "post_publication_availability_v2.py"
    )
    post_read_batch_consumer = APP / "mie" / "validation" / "post_read_batch_v3.py"
    post_read_batch_imports = {
        "app.public_market_source.public_receipt_storage",
        "app.public_market_source.public_receipt_storage.ControlledPublicReceiptJournal",
    }
    post_publication_imports = {
        "app.public_market_source.public_market_capture",
        "app.public_market_source.public_market_capture.replay_public_capture",
        "app.public_market_source.public_market_receipts",
        "app.public_market_source.public_market_receipts.canonical",
        "app.public_market_source.public_market_receipts.sha",
        "app.public_market_source.public_market_receipts.utc_from_ns",
        "app.public_market_source.public_receipt_storage",
        "app.public_market_source.public_receipt_storage.ControlledPublicReceiptJournal",
    }
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
        # The pure schedule join converts pinned nanoseconds to UTC only; it
        # owns no market I/O or additional public-source capability.
        APP / "mie" / "validation" / "blind_window_schedule_binding.py": (
            "public_market_receipts",
        ),
        # Pure future-window planning reuses the exact V1 minute-plan contract;
        # this consumer owns no market I/O or execution authority.
        APP / "mie" / "validation" / "prospective_capture_schedule.py": (
            "public_market_receipts",
        ),
        # Post-publication availability replays retained bytes only; it cannot
        # acquire a fresh public source or gain exchange write authority.
        post_publication_consumer: (
            "public_market_capture",
            "public_market_receipts",
            "public_receipt_storage",
        ),
        # The V3 computational batch names only the offline journal contract;
        # no acquisition module or additional storage capability is imported.
        post_read_batch_consumer: ("public_receipt_storage",),
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
            "public_receipt_storage",
            "public_runtime_journal",
        ),
    }
    observed = set()
    observed_post_publication_imports = set()
    observed_post_read_batch_imports = set()
    for path in APP.rglob("*.py"):
        if path.is_relative_to(SOURCE):
            continue
        for node in nodes(path):
            for name in names(node):
                if name.startswith("app.public_market_source"):
                    assert path in allowed, (path, name)
                    if path == post_publication_consumer:
                        assert name in post_publication_imports, name
                        observed_post_publication_imports.add(name)
                    if path == post_read_batch_consumer:
                        assert name in post_read_batch_imports, name
                        assert (
                            isinstance(node, ast.ImportFrom)
                            and node.module
                            == "app.public_market_source.public_receipt_storage"
                            and len(node.names) == 1
                            and node.names[0].name == "ControlledPublicReceiptJournal"
                            and node.names[0].asname is None
                        ), name
                        observed_post_read_batch_imports.add(name)
                    if path.name == "blind_window_dataset.py" and name.startswith(
                        "app.public_market_source.public_market_capture"
                    ):
                        assert name in {
                            "app.public_market_source.public_market_capture",
                            "app.public_market_source.public_market_capture.replay_public_capture",
                        }, name
                    if path.name == "blind_window_schedule_binding.py":
                        assert name in {
                            "app.public_market_source.public_market_receipts",
                            "app.public_market_source.public_market_receipts.utc_from_ns",
                        }, name
                    assert any(
                        name == f"app.public_market_source.{module}"
                        or name.startswith(f"app.public_market_source.{module}.")
                        for module in allowed[path]
                    ), name
                    observed.add(path)
    assert observed == set(allowed)
    assert observed_post_publication_imports == post_publication_imports
    assert observed_post_read_batch_imports == post_read_batch_imports


def test_post_publication_rejects_unreviewed_public_capture_import(monkeypatch):
    original_nodes = nodes
    consumer = APP / "mie" / "validation" / "post_publication_availability_v2.py"
    unreviewed = ast.parse(
        "from app.public_market_source.public_market_capture import capture_public_market"
    ).body[0]

    def with_unreviewed_import(path):
        yield from original_nodes(path)
        if path == consumer:
            yield unreviewed

    monkeypatch.setattr(sys.modules[__name__], "nodes", with_unreviewed_import)
    with pytest.raises(
        AssertionError,
        match="app.public_market_source.public_market_capture.capture_public_market",
    ):
        test_public_source_has_only_explicit_reviewed_consumers()


@pytest.mark.parametrize(
    "import_line",
    [
        "from app.public_market_source.public_receipt_storage import OwnedPublicReceiptPublisher",
        "import app.public_market_source.public_receipt_storage",
        "from app.public_market_source.public_market_capture import capture_public_market",
    ],
)
def test_post_read_batch_rejects_any_extra_public_source_import(
    monkeypatch, import_line
):
    original_nodes = nodes
    consumer = APP / "mie" / "validation" / "post_read_batch_v3.py"
    unreviewed = ast.parse(import_line).body[0]

    def with_unreviewed_import(path):
        yield from original_nodes(path)
        if path == consumer:
            yield unreviewed

    monkeypatch.setattr(sys.modules[__name__], "nodes", with_unreviewed_import)
    with pytest.raises(AssertionError):
        test_public_source_has_only_explicit_reviewed_consumers()


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
