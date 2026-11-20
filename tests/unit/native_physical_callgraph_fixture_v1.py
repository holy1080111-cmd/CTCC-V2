"""Real vendor callgraph against loopback TLS; all ownership/SQL ingress synthetic.

No configured key, operator Arm, database connection or venue write is possible.
The original worker, math, source journals, entry proof, physical gate, HTTPX,
HTTPCore, AnyIO, SSL and native socket implementations remain in the callgraph.
"""

from __future__ import annotations

import asyncio
import dis
import os
import shutil
import socket
import ssl
import subprocess
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from threading import get_ident
from time import perf_counter_ns
from types import SimpleNamespace as Data

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.native_clock import native_stamp as observed_native_stamp
from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.exchange.okx import native_live_write_v1 as physical
from app.okx_live import owned_qualification_v1 as q
from tests.unit.native_entry_worker_fixture_v1 import WorkerFixture
from tests.unit.research.test_owned_public_runtime_p1 import SyntheticClock

_REAL_SUBMIT = physical.submit_owned_live_fok_v1
_REAL_NEW_GATE = physical._new_gate
_REAL_SESSION = q._session


def ephemeral_certificate(root):
    """Generate a fresh test-only CA/key inside the pytest-owned private leaf."""
    executable = shutil.which("openssl")
    if executable is None and os.name == "nt":
        candidate = (
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Git/usr/bin/openssl.exe"
        )
        if candidate.is_file():
            executable = str(candidate)
    if executable is None:
        raise ValueError("synthetic_loopback_openssl_required")
    config, key, cert = (
        root / "loopback.cnf",
        root / "loopback.key",
        root / "loopback.pem",
    )
    config.write_text(
        "[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n"
        "[dn]\nCN=openapi.okx.com\n[ext]\nsubjectAltName=DNS:openapi.okx.com\n"
        "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n",
        encoding="ascii",
    )
    result = subprocess.run(
        [
            executable,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-config",
            str(config),
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=False,
        capture_output=True,
        timeout=20,
    )
    if result.returncode != 0 or not key.is_file() or not cert.is_file():
        raise ValueError("synthetic_loopback_certificate_refused")
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.minimum_version = ssl.TLSVersion.TLSv1_2
    server.load_cert_chain(cert, key)
    return cert, server


def install_native_pacing(monkeypatch):
    """Historical synthetic UTC advances by at least real native elapsed time."""
    first, last = perf_counter_ns(), 0

    def stamp(clock):
        nonlocal last
        last = max(
            last + 1_000_000,
            ((perf_counter_ns() - first + 999_999) // 1_000_000) * 1_000_000,
        )
        clock.count = last // 1_000_000
        clock.last = utc_from_ns(clock.base + last)
        return {"utc_ns": clock.base + last, "monotonic_ns": last}

    monkeypatch.setattr(SyntheticClock, "stamp", stamp)


class _Result:
    def __init__(self, rows=()):
        self.rows = rows

    def mappings(self):
        return self

    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]

    def all(self):
        return list(self.rows)


class PhysicalWorkerFixture(WorkerFixture):
    async def refresh(self, session, barrier):
        if (
            getattr(self, "preserve_stale_current_instrument", False)
            and "publication" in self.state
        ):
            # An explicit native latency fault, before all NEW account facts.
            # Keep the old spec unchanged; never move a retained timestamp.
            await asyncio.sleep(6)
        result = await super().refresh(session, barrier)
        self.view["lease"]._pin = Data(origin="https://openapi.okx.com")
        self.view["current_readback"] = Data(
            receipt={"no_open_positions_or_pending_orders_observed": True},
            usdt_available_equity=Data(available_equity=Decimal(1000)),
        )
        self._current_pages()
        return result

    def _current_pages(self):
        previous = self.state["instrument"]
        spec = previous.spec
        instrument_id = self.state["instrument_id"]
        instrument = {
            "instId": instrument_id,
            "instType": "SWAP",
            "ctType": "linear",
            "settleCcy": "USDT",
            "state": "live",
            "ctMult": "1",
            "ctVal": str(spec.contract_value),
            "ctValCcy": spec.contract_value_currency,
            "lotSz": str(spec.lot_size),
            "minSz": str(spec.min_contracts),
            "maxLmtSz": str(spec.max_contracts),
            "tickSz": str(self.state["instrument"].tick_size),
        }
        pages = []
        for selector, rows in (
            (
                q.source.current.select_live_account_raw_page_v1(
                    "account_instruments", instrument_id=instrument_id
                ),
                [instrument],
            ),
            (
                q.source.current.select_live_account_raw_page_v1(
                    "leverage_isolated", leverage_instrument_ids=(instrument_id,)
                ),
                [
                    {
                        "instId": instrument_id,
                        "mgnMode": "isolated",
                        "posSide": "net",
                        "lever": str(previous.leverage),
                    }
                ],
            ),
        ):
            # Declare a NEW synthetic account-response record at this refresh,
            # before deriving its metadata. These are not retained venue pages.
            stamps = tuple(self.stamp() for _ in range(4))
            raw = canonical({"code": "0", "data": rows})
            pages.append(
                Data(
                    selector=selector,
                    raw_body=raw,
                    raw_sha256=sha(raw),
                    page_sha256=sha(
                        canonical(
                            [
                                "declared-new-synthetic-current-page-v1",
                                selector.stream,
                                sha(raw),
                                [stamp.model_dump() for stamp in stamps],
                            ]
                        )
                    ),
                    stamps=stamps,
                )
            )
        self.view["current_pages"] = tuple(pages)
        self.view["current_completed"] = self.stamp()
        self.native_phases.append(("account_completed", observed_native_stamp()))
        if (
            getattr(self, "preserve_stale_current_instrument", False)
            and "publication" in self.state
        ):
            return
        # The inherited worker adapter intentionally has synthetic admission.
        # Parse the NEW declared body into a NEW typed spec. Preserve the exact
        # economic identity; only new response identity/receipt times differ.
        page = pages[0]
        row = q.source.current._rows(page.raw_body)[0]
        fields = spec.model_dump(mode="python") | {
            "contract_value": q.adapter._number(row, "ctVal"),
            "lot_size": q.adapter._number(row, "lotSz"),
            "min_contracts": q.adapter._number(row, "minSz"),
            "max_contracts": q.adapter._number(row, "maxLmtSz"),
            "source_sha256": sha(canonical([page.raw_sha256, page.page_sha256])),
            "observed_at": utc_from_ns(page.stamps[3].utc_ns),
            "received_at": utc_from_ns(self.view["current_completed"].utc_ns),
        }
        refreshed = q.adapter.LiveInstrumentDataV1(
            type(spec).model_validate(fields, strict=True),
            q.adapter._number(row, "tickSz"),
            previous.contracts,
            previous.leverage,
            previous.position_side,
        )
        assert q._instrument_identity(refreshed) == q._instrument_identity(previous)
        self.state["instrument"] = refreshed

    def install_physical(self, cert, monkeypatch):
        # Retain the ORIGINAL task/session callgraph. Only environment/settings
        # admission remains inert; a per-qualified guard count is not assumed.
        self.state.update(
            loop=asyncio.get_running_loop(),
            pid=os.getpid(),
            thread=get_ident(),
            closed=False,
        )
        monkeypatch.setattr(q, "_session", _REAL_SESSION)
        monkeypatch.setattr(q, "_settings_pin_v1", lambda _settings: b"{}")
        monkeypatch.setattr(q.source, "_configured", lambda _plan: None)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_verify_locations(cert)
        engine = create_async_engine(
            "postgresql+asyncpg://synthetic_writer:synthetic@127.0.0.1:1/synthetic",
            poolclass=NullPool,
            hide_parameters=True,
            connect_args={"ssl": context},
        )
        self.engine = engine
        self.database_connect_attempts = []

        def refuse_database(_dialect, _record, _args, _kwargs):
            self.database_connect_attempts.append(True)
            raise AssertionError(
                "actual_database_connection_forbidden_in_synthetic_callgraph"
            )

        event.listen(engine.sync_engine, "do_connect", refuse_database)
        self.factory = async_sessionmaker(
            engine, autoflush=False, expire_on_commit=False
        )
        self.pins = physical.ledger.LiveLedgerRolePinsV1(
            101, 102, 103, "synthetic_writer"
        )
        self.cfg.update(writer_factory=self.factory, writer_pins=self.pins)
        self.intent = None
        self.observer = None
        self.journal_json = canonical({"schema": "synthetic_sql_transition_only"})
        outer = self
        self.disarmed = False
        self.stopped = False

        class Ledger:
            def __init__(self, factory, pins):
                assert factory is outer.factory and pins is outer.pins

            async def reserve_and_commit_intent_data_v1(self, value, *, owned_source):
                assert owned_source is outer.source
                outer.intent = value
                outer.operations += ["durable-commit", "independent-readback"]
                rid, _raw, digest = q.exact_live_intent_document_v1(value)
                if outer.observer is not None:
                    outer.observer.start_counts()
                return {
                    "schema": "ctcc.native_live.intent_readback_data.v1",
                    "reservation_id": rid,
                    "intent_sha256": digest,
                    "state": "reserved",
                    "journal_sha256": sha(outer.journal_json),
                    "qualification_verified": False,
                    "execution_authority": False,
                    "admission": "DENY",
                }

        async def profile(session, pins):
            assert type(session) is AsyncSession and session.bind is outer.engine
            assert pins is outer.pins and session.in_transaction()

        async def execute(session, statement, params=None, **_kwargs):
            assert type(session) is AsyncSession and session.bind is outer.engine
            assert session.in_transaction()
            sql = str(statement)
            if sql.startswith("SET ") or "pg_advisory_xact_lock" in sql:
                return _Result()
            if "native_live_control_fence_v1" in sql:
                return _Result((dict(outer.state["control"]),))
            value = outer.intent
            if "FROM public.native_live_reservations_v1 r" in sql:
                _rid, raw, digest = q.exact_live_intent_document_v1(value)
                return _Result(
                    (
                        (
                            "live",
                            value.account_uid,
                            value.event_key,
                            value.candidate_sha256,
                            value.report_sha256,
                            value.source_revision_sha256,
                            value.expected_control_revision,
                            value.arm_id,
                            max(value.risk_samples),
                            value.margin_amount,
                            value.notional_amount,
                            value.deadline,
                            "reserved",
                            raw.decode(),
                            digest,
                            outer.journal_json.decode(),
                            sha(outer.journal_json),
                        ),
                    )
                )
            if "COALESCE(sum(risk_amount)" in sql:
                return _Result(
                    (
                        (
                            max(value.risk_samples),
                            value.margin_amount,
                            value.notional_amount,
                        ),
                    )
                )
            if "SELECT main_uid,process_sha256,armed" in sql:
                assert outer.disarmed
                return _Result(
                    (
                        (
                            value.main_uid,
                            value.process_sha256,
                            False,
                            None,
                            None,
                            outer.stopped,
                            value.expected_control_revision + 1,
                            value.credential_session_sha256,
                            value.policy_sha256,
                        ),
                    )
                )
            raise AssertionError("unexpected_synthetic_sql_query")

        async def scalar(session, statement, params=None, **_kwargs):
            assert type(session) is AsyncSession and session.bind is outer.engine
            sql = str(statement)
            if sql == "SELECT clock_timestamp()":
                return utc_from_ns(outer.stamp().utc_ns)
            if "native_live_disarm_v1" in sql:
                outer.disarmed = True
                return outer.intent.expected_control_revision + 1
            if "native_live_inhibit_v1" in sql:
                outer.disarmed = True
                outer.stopped = True
                return outer.intent.expected_control_revision + 1
            if "pg_try_advisory_xact_lock(1680010)" in sql or sql == str(
                physical.require_legacy_live_negative_fence_v1.__globals__["CLEAR_SQL"]
            ):
                return True
            raise AssertionError("unexpected_synthetic_sql_scalar")

        def new_gate(record):
            gate = _REAL_NEW_GATE(record)
            # Test trust anchor is confined to this ephemeral gate context.
            physical._record(gate)["tls_context"].load_verify_locations(cert)
            return gate

        monkeypatch.setattr(q.ledger, "NativeLiveLedgerDataRepositoryV1", Ledger)
        monkeypatch.setattr(physical.ledger, "_profile", profile)
        monkeypatch.setattr(AsyncSession, "execute", execute)
        monkeypatch.setattr(AsyncSession, "scalar", scalar)
        monkeypatch.setattr(physical, "_stamp", self.stamp)
        monkeypatch.setattr(physical, "_new_gate", new_gate)
        monkeypatch.setattr(physical, "_COORDINATOR_BINDINGS", None)
        monkeypatch.setattr(physical, "submit_owned_live_fok_v1", _REAL_SUBMIT)
        material = q.source._LiveCredentialMaterialV1(
            ("synthetic-no-key", "synthetic-no-secret", "synthetic-no-passphrase"),
            "https://openapi.okx.com",
            "CTCC-synthetic-loopback-only",
            10,
        )
        monkeypatch.setattr(
            q.source, "_owned_live_credential_material_v1", lambda *_a, **_k: material
        )


class CallgraphObservation:
    def __init__(self):
        self.events, self.counter, self.refusals = [], Counter(), []
        self.first_application_enqueue = None
        self.tool = None
        self.counting = False
        functions = {
            "qualified": q._qualified,
            "facts": physical._facts,
            "physical_gate": physical._require_physical_gate_v1,
            "final_clock": q.entry_proof.require_native_final_clock_v1,
            "held_readback": physical._held_readback,
            "transport_gate": physical.transport._gate,
            "ssl_write": physical._PhysicalLiveWriteGateV1.ssl_write,
            "raw_write": physical._PhysicalLiveWriteGateV1.raw_write,
            "http_transport": physical.transport._Transport.handle_async_request,
            "connect_tcp": physical.transport._Backend.connect_tcp,
            "start_tls": physical.transport._ConnectionStream.start_tls,
            "physical_need": physical._need,
            "qualified_need": q._need,
            "proof_need": q.entry_proof._need,
            "ledger_need": physical.ledger._need,
        }
        self.codes = {function.__code__: name for name, function in functions.items()}
        self.native_write_codes = {
            function.__code__
            for _kind, name, function in physical.transport._VENDOR_BINDINGS_V1
            if name == "write" and hasattr(function, "__code__")
        }
        self.raise_lines = {
            function.__code__: {
                instruction.positions.lineno
                for instruction in dis.get_instructions(function)
                if instruction.opname == "RAISE_VARARGS"
            }
            for function in (
                q._need,
                physical._need,
                q.entry_proof._need,
                physical.ledger._need,
            )
        }

    def start_faults(self):
        """Only local raise-line events run before the durable intent seam."""
        self.codes[q._NATIVE_GUARD.__code__] = "inert_source_guard"
        monitor = sys.monitoring
        if monitor.get_tool(3) is not None:
            raise AssertionError("synthetic_monitoring_tool_already_in_use")
        monitor.use_tool_id(3, "ctcc-synthetic-loopback-observer")
        self.tool = 3
        monitor.register_callback(3, monitor.events.LINE, self.line)
        monitor.register_callback(3, monitor.events.PY_START, self.start)
        monitor.register_callback(3, monitor.events.PY_RETURN, self.returned)
        monitor.set_local_events(3, q._need.__code__, monitor.events.LINE)

    def start_counts(self):
        """Observe original code entries only after synthetic committed readback."""
        if self.tool is None:
            return
        assert not self.counting
        self.counting = True
        monitor = sys.monitoring
        for code in self.codes:
            events = monitor.events.PY_START
            if code in self.raise_lines:
                events |= monitor.events.LINE
            monitor.set_local_events(self.tool, code, events)
        for code in self.native_write_codes:
            monitor.set_local_events(self.tool, code, monitor.events.PY_RETURN)

    def close(self):
        if self.tool is not None:
            monitor, tool = sys.monitoring, self.tool
            for code in self.codes.keys() | self.native_write_codes:
                monitor.set_local_events(tool, code, 0)
            for event in (
                monitor.events.LINE,
                monitor.events.PY_START,
                monitor.events.PY_RETURN,
            ):
                monitor.register_callback(tool, event, None)
            monitor.free_tool_id(tool)
            self.tool = None

    def returned(self, code, _offset, _result):
        if code in self.native_write_codes:
            frame = sys._getframe(1)
            gate = physical._ACTIVE.get()
            retained = physical._GATES.get(gate) if gate is not None else None
            if (
                type(retained) is dict
                and retained.get("tls_ready") is True
                and retained.get("attempted") is True
                and frame.f_locals.get("self") is retained.get("socket_transport")
                and self.first_application_enqueue is None
            ):
                self.first_application_enqueue = observed_native_stamp()

    def start(self, code, _offset):
        name = self.codes[code]
        self.counter[name] += 1
        if not name.endswith("_need"):
            self.events.append((name, observed_native_stamp()))

    def line(self, code, number):
        if number in self.raise_lines.get(code, ()):
            caller = sys._getframe(1).f_back
            self.refusals.append(
                {
                    "helper": self.codes[code],
                    "caller": caller.f_code.co_name if caller is not None else None,
                    "line": caller.f_lineno if caller is not None else None,
                }
            )


async def loopback_server(server_context):
    completed = asyncio.Event()
    counts = {
        "local_synthetic_http_posts": 0,
        "accepted_local_tls_connections": 0,
        "first_post_byte": None,
        "body_complete": None,
    }

    async def respond(reader, writer):
        counts["accepted_local_tls_connections"] += 1
        try:
            first = await reader.readexactly(1)
            counts["first_post_byte"] = observed_native_stamp()
            headers = first + await reader.readuntil(b"\r\n\r\n")
            assert headers.startswith(b"POST /api/v5/trade/order HTTP/1.1\r\n")
            fields = {
                line.split(b":", 1)[0].lower(): line.split(b":", 1)[1].strip()
                for line in headers.split(b"\r\n")[1:]
                if b":" in line
            }
            length = int(fields[b"content-length"])
            assert 0 < length <= 8192
            body = await reader.readexactly(length)
            document = decode(body)
            assert document["ordType"] == "fok" and document["tdMode"] == "isolated"
            counts["local_synthetic_http_posts"] += 1
            counts["body_complete"] = observed_native_stamp()
            reply = canonical(
                {
                    "code": "0",
                    "data": [
                        {
                            "ordId": "900001",
                            "clOrdId": document["clOrdId"],
                            "sCode": "0",
                            "sMsg": "",
                        }
                    ],
                }
            )
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(reply)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + reply
            )
            await writer.drain()
        except (
            OSError,
            ValueError,
            AssertionError,
            asyncio.IncompleteReadError,
        ) as error:
            counts["server_failure_class"] = type(error).__name__
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ssl.SSLError):
                counts["server_close_unconfirmed"] = True
            finally:
                completed.set()

    # AnyIO preserves the original remote port after resolving the address.
    # Bind that exact port; never patch a vendor connect/socket implementation,
    # change host routing or acquire privilege if this scoped bind is refused.
    server = await asyncio.start_server(respond, "127.0.0.1", 443, ssl=server_context)
    port = server.sockets[0].getsockname()[1]
    return server, port, completed, counts


def install_loopback_dns(monkeypatch, port):
    assert port == 443

    def resolve(host, service, *_args, **_kwargs):
        if isinstance(host, bytes):
            host = host.decode("ascii")
        if host != "openapi.okx.com" or service != 443:
            raise AssertionError("non_loopback_synthetic_route_refused")
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("127.0.0.1", port),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
