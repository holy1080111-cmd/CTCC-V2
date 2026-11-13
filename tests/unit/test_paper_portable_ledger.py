"""Read and independently replay a real loopback portable Paper export."""

import hmac
import json
import os
from collections import deque
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

import pytest

from app.database.repositories.persistence import state_checksum
from app.domain.paper import PaperOrderRequest, PaperStateView
from app.domain.source_primitives import canonical, sha
from app.paper.engine import PaperBroker
from app.paper.portable_state import PortablePaperStateError
from scripts import paper_portable_ledger as tool

TOKEN = b"a" * 64
PROFILE = {
    "version": "1.6.9",
    "environment": "production",
    "trading_mode": "analysis_only",
    "auto_trade": False,
    "live_trading": False,
}


def paper_state():
    broker = PaperBroker()
    request = PaperOrderRequest(
        symbol="BTC-USDT-SWAP",
        side="long",
        quantity="1",
        reference_price="100",
        stop_loss="90",
        take_profit="110",
        client_order_id="fixture-buy-01",
    )
    order = broker.submit(request)
    broker.close(broker.state().positions[0].id, price=Decimal(102))
    pending = broker.submit(
        request.model_copy(
            update={
                "client_order_id": "fixture-cancel-02",
                "order_type": "limit",
                "limit_price": Decimal(95),
            }
        )
    )
    broker.cancel(pending.id)
    assert order.status == "filled"
    return broker.state()


@pytest.fixture
def runtime():
    return {
        "runtime_id": str(uuid4()),
        "installation_binding": hmac.digest(TOKEN, tool._BINDING_LABEL, "sha256").hex(),
        "persistent": True,
        "recovered": True,
        "state": paper_state().model_dump(mode="json"),
    }


@pytest.fixture
def private_dirs(tmp_path):
    if os.name == "nt":
        # Exercise the actual Windows owner-protected working directory.
        from scripts.local_analysis_private_dir import create_private_work

        private = create_private_work()
        output = create_private_work()
    else:
        private, output = tmp_path / "private", tmp_path / "output"
        private.mkdir(mode=0o700)
        output.mkdir(mode=0o700)
    token = private / "api_token"
    try:
        if os.name == "nt":
            from app.trade_evidence import storage
            from app.trade_evidence.native_private_root_v2 import (
                _open_new_windows_private_evidence_file,
                _windows_private,
            )

            # Windows may otherwise give a file created by an administrator
            # the Administrators group as owner, even in a user-owned directory.
            with storage._windows_root(private) as directory:
                fd = _open_new_windows_private_evidence_file(directory, token.name)
                with os.fdopen(fd, "wb") as handle:
                    _windows_private(token)
                    handle.write(TOKEN)
        else:
            with token.open("xb") as handle:
                handle.write(TOKEN)
            token.chmod(0o600)
        yield token, output
    finally:
        for path in (output / tool._EXPORT, token):
            path.unlink(missing_ok=True)
        private.rmdir()
        output.rmdir()


def serve(monkeypatch, entries):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            reads.append((self.command, self.path, self.headers.get("X-CTCC-Token")))
            status, body, extra = queue.popleft()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for name, value in extra.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

    reads = []
    queue = deque(entries)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    monkeypatch.setattr(tool, "_PORT", server.server_port)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, reads


def test_cold_portable_inspection_keeps_complete_paper_lineage(
    monkeypatch, runtime, private_dirs, capsys
):
    token, output = private_dirs
    server, thread, reads = serve(
        monkeypatch,
        [(200, canonical(value), {}) for value in (PROFILE, runtime, runtime, PROFILE)],
    )
    try:
        result = tool.export(token, output)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    exported = output / tool._EXPORT
    raw = exported.read_bytes()
    assert TOKEN not in raw
    assert runtime["runtime_id"].encode() not in raw
    assert runtime["installation_binding"].encode() not in raw
    assert json.loads(raw)["state"] == runtime["state"]
    state = PaperStateView.model_validate_json(canonical(runtime["state"]))
    assert result["durable_state_sha256"] == state_checksum(state)
    assert (
        result["order_count"],
        result["position_count"],
        result["closed_positions"],
    ) == (2, 1, 1)
    assert result["execution_authority"] is result["restore_authority"] is False
    assert [(method, path, header is not None) for method, path, header in reads] == [
        ("GET", tool._VERSION, False),
        ("GET", tool._RUNTIME, True),
        ("GET", tool._RUNTIME, True),
        ("GET", tool._VERSION, False),
    ]
    assert tool.inspect(output, expected_sha256=sha(raw)) == result
    assert tool.main(["inspect", "--directory", str(output), "--sha256", "b" * 64]) == 1
    assert TOKEN.decode() not in capsys.readouterr().out
    with pytest.raises(PortablePaperStateError):
        tool.export(token, output)
    assert exported.read_bytes() == raw


@pytest.mark.parametrize(
    "defect",
    [
        "redirect",
        "wrong_binding",
        "runtime_change",
        "same_state_different_runtime",
        "version_change",
        "credentials_allowed",
        "nonpersistent",
    ],
)
def test_export_refuses_unverified_or_unstable_source(
    monkeypatch, runtime, private_dirs, defect
):
    first, second = runtime, runtime.copy()
    before, after = PROFILE, PROFILE
    response, headers = 200, {}
    if defect == "redirect":
        response, headers = 302, {"Location": "https://unexpected.test"}
    elif defect == "wrong_binding":
        first = {**first, "installation_binding": "3" * 64}
    elif defect == "runtime_change":
        second["state"] = PaperBroker().state().model_dump(mode="json")
    elif defect == "same_state_different_runtime":
        second["runtime_id"] = str(uuid4())
    elif defect == "version_change":
        after = {**after, "version": "1.7.0"}
    elif defect == "credentials_allowed":
        before = {**before, "live_trading": True}
    else:
        first = {**first, "persistent": False}
    server, thread, _ = serve(
        monkeypatch,
        [
            (response, canonical(before), headers),
            (200, canonical(first), {}),
            (200, canonical(second), {}),
            (200, canonical(after), {}),
        ],
    )
    try:
        token, output = private_dirs
        with pytest.raises(PortablePaperStateError):
            tool.export(token, output)
        assert not (output / tool._EXPORT).exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize(
    "defect",
    [
        "foreign_field",
        "duplicated_order",
        "broken_join",
        "incorrect_aggregate",
        "wrong_restart_checksum",
        "authority",
    ],
)
def test_ledger_rejects_malformed_even_with_known_whole_export_digest(runtime, defect):
    raw = tool.portable.make_export(runtime, software_version=PROFILE["version"])
    data = json.loads(raw)
    if defect == "foreign_field":
        data["state"]["orders"][0]["foreign"] = 1
    elif defect == "duplicated_order":
        data["state"]["orders"].append(data["state"]["orders"][0])
    elif defect == "broken_join":
        data["state"]["positions"][0]["order_id"] = str(uuid4())
    elif defect == "incorrect_aggregate":
        data["state"]["account"]["pending_orders"] = 42
    elif defect == "wrong_restart_checksum":
        data["durable_state_sha256"] = "b" * 64
    else:
        data["execution_authority"] = True
    forged = canonical(data)
    with pytest.raises(ValueError):
        tool.portable.inspect_export(forged, expected_sha256=sha(forged))
