"""Explicit synthetic admission/SQL seams around the ORIGINAL worker body.

There is no configured runtime, real credential, database, operator Arm or POST.
Raw market capture, G1--G11, G12 six-file publication/readback, fixed R7,
intent construction, original-frame proof and final guard computation are real.
"""

import asyncio
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from time import perf_counter
from types import SimpleNamespace as Data

from app.domain.native_clock import native_stamp as observed_native_stamp
from app.domain.source_primitives import ClockStamp, canonical, sha, utc_from_ns
from app.okx_live import owned_qualification_v1 as q
from app.trade_qualification.engine import PreEvidencePolicy
from tests.native_private_temp_v1 import _private_root_path
from tests.unit.qualification_engine_fixtures import engine_inputs
from tests.unit.research import test_native_live_public_source_v1 as public_fixture
from tests.unit.research.test_owned_public_runtime_p1 import SyntheticClock
from tests.unit.test_live_qualification_math_finite_v1 import _declared_finite_options


class WorkerFixture:
    def __init__(self, monkeypatch, tmp_path, market):
        self.patch, self.tmp, self.market = monkeypatch, tmp_path, market
        self.stack = ExitStack()
        self.clock = SyntheticClock(market.evaluated_at)
        self.banks, self.roots, self.sources = {}, {}, []
        self.operations, self.guard_times = [], []
        self.native_phases, self.native_arrivals = [], []
        self.session = object.__new__(q.OwnedLiveOperatorSessionV1)
        self.source = object.__new__(q.source.OwnedLiveSourceV1)
        self.source_owners = [self.source]
        self.guard_count = 32
        self.financial = object.__new__(q.financial.OwnedLiveFinancialEpochV1)
        self.factory, self.pins, self.runtime = object(), object(), object()
        args = engine_inputs(market)
        portfolio = args["policy"].portfolio.model_copy(
            update={
                "max_age_seconds": 5,
                "drawdown_window_started_at": market.evaluated_at - timedelta(days=1),
            }
        )
        policy = PreEvidencePolicy(
            **(args["policy"].model_dump(mode="python") | {"portfolio": portfolio})
        )
        future = asyncio.get_running_loop().create_future()
        future.set_result(
            ("fixture", q.ARM_PHRASE + ":fixture", q.CLEAR_STOP_PHRASE + ":fixture", "")
        )
        self.state = {
            "task": asyncio.current_task(),
            "runtime": self.runtime,
            "invocation": object(),
            "source_owner": self.source,
            "financial_owner": self.financial,
            "run_started": False,
            "approval_used": False,
            "approval_future": future,
            "challenge_id": "fixture",
            "nonce": "fixture",
            "challenge_expiry_monotonic_ns": 120_000_000_000,
            "challenge_json": b"{}",
            "exit_requested": False,
            "policy": policy,
            "policy_sha256": q.policy_hash(policy),
            "process_sha256": "c" * 64,
            "settings_json": b"{}",
            "instrument_id": args["intent"].instrument_id,
            "strategy": args["intent"].strategy,
            "direction": args["intent"].direction,
            "report_id": "fixed-live-report",
            "caps": (Decimal("0.25"), Decimal("0.25"), Decimal(25), Decimal(25)),
            "active": False,
            "public_owners": [],
            "last": self.stamp(),
        }
        self.view = {
            "uid": "123456",
            "main_uid": "123455",
            "credential_session_sha256": "b" * 64,
            "source_revision_sha256": "c" * 64,
        }
        self.state["identity"] = dict(self.view)
        self.view.update(
            task=asyncio.current_task(),
            lease=Data(verify_current=lambda: None),
            plan=Data(max_current_age_ns=5_000_000_000),
        )
        q.source._SOURCES[self.source] = self.view
        q._OWNED[self.session] = self.state
        self.evidence_root = _private_root_path(tmp_path)
        self.cfg = {
            "writer_factory": self.factory,
            "writer_pins": self.pins,
            "evidence_root": self.evidence_root,
            "public_root": tmp_path / "public",
        }
        self._install()

    def stamp(self):
        return ClockStamp.model_validate(self.clock.stamp(), strict=True)

    def _session(self, owner, *, current=False, negative=False):
        q._need(owner is self.session and asyncio.current_task() is self.state["task"])
        q._need(negative or not asyncio.current_task().cancelling())
        now = self.stamp()
        q.validate_stamps((self.state["last"].model_dump(), now.model_dump()))
        self.state["last"] = now
        if current:
            q._need(
                self.state["active"]
                and now.monotonic_ns < self.state["expiry_monotonic_ns"]
                and utc_from_ns(now.utc_ns) < self.state["deadline"]
            )
        return self.state

    async def refresh(self, session, barrier):
        assert session is self.session
        self.operations.append("fresh-account")
        previous = self.source
        self.source = object.__new__(q.source.OwnedLiveSourceV1)
        self.source_owners.append(self.source)
        self.view = self.view | {
            "previous_source_owner": previous,
            "source_revision_sha256": sha(str(len(self.source_owners)).encode()),
            "plan": Data(max_current_age_ns=5_000_000_000, publication_barrier=barrier),
        }
        q.source._SOURCES[self.source] = self.view
        self.state["source_owner"] = self.source
        self.state["identity"] = {
            name: self.view[name]
            for name in (
                "uid",
                "main_uid",
                "credential_session_sha256",
                "source_revision_sha256",
            )
        }
        at = utc_from_ns(self.stamp().utc_ns)
        f = q.financial.LiveFinancialWindowFactsV1(
            self.view["source_revision_sha256"],
            "123456",
            "123455",
            "b" * 64,
            "d" * 64,
            1,
            self.market.evaluated_at - timedelta(days=1),
            at,
            at,
            at,
            at - timedelta(days=8),
            at,
            Fraction(),
            Fraction(),
            0,
            Fraction(1000),
            Fraction(1000),
            at,
            Fraction(1000),
            (),
            (),
            (),
            b"{}",
        )
        self.view["current_completed"] = self.stamp()
        self.native_phases.append(("account_completed", observed_native_stamp()))
        q.financial._OWNED[self.financial] = {
            "task": asyncio.current_task(),
            "source": self.source,
            "sample_json": f.receipt_json,
            "epoch_json": b"{}",
            "factory": self.factory,
            "pins": self.pins,
        }
        self.facts = f
        return f

    async def control(self, session, action):
        assert session is self.session
        self.operations.append(action)
        if action == "arm":
            self.native_phases.append(("arm", observed_native_stamp()))
        at = utc_from_ns(self.stamp().utc_ns)
        self.state["control"] = {
            "environment": "live",
            "account_uid": self.view["uid"],
            "main_uid": self.view["main_uid"],
            "credential_session_sha256": self.view["credential_session_sha256"],
            "source_revision_sha256": self.view["source_revision_sha256"],
            "policy_sha256": self.state["policy_sha256"],
            "process_sha256": self.state["process_sha256"],
            "revision": 7,
            "armed": action in ("arm", "refresh"),
            "emergency_stop": False,
            "arm_id": self.state.get("arm_id"),
            "updated_at": at,
            "arm_expires_at": self.state.get("deadline"),
            "lease_until": at + timedelta(minutes=2),
            "per_trade_risk_cap": Decimal("0.25"),
            "portfolio_risk_cap": Decimal("0.25"),
            "margin_cap": Decimal(25),
            "micro_notional_cap": Decimal(25),
        }
        row = canonical(
            {
                k: v.isoformat()
                if hasattr(v, "isoformat")
                else str(v)
                if isinstance(v, Decimal)
                else v
                for k, v in self.state["control"].items()
            }
        )
        self.state["control_readback"] = q.controls.LiveOperatorControlReadbackV1(
            row, sha(row)
        )

    def _public_fixture(self, stage):
        self.operations.append(stage)
        patch = self.stack.enter_context(self.patch.context())
        fixture = self.stack.enter_context(
            contextmanager(public_fixture.live_source.__wrapped__)(
                patch, self.market, self.tmp / stage
            )
        )
        old = self.clock
        self.clock = fixture[0]
        self.clock.count = old.count + 10
        _, parent, raw, _, key, request = fixture
        harness = fixture[3]
        original_public, original_recv = harness.public, harness.socket.recv

        def observe_request(value):
            self.native_arrivals.append(
                (stage, value.url.path, observed_native_stamp())
            )
            return original_public(value)

        async def observe_ws():
            value = await original_recv()
            self.native_arrivals.append((stage, "ws", observed_native_stamp()))
            return value

        harness.public, harness.socket.recv = observe_request, observe_ws
        self.banks[parent.path], self.banks[raw.path] = parent, raw
        self.roots[parent.path], self.roots[raw.path] = (1, 1), (2, 2)
        original_coordinator = q.public._coordinator
        patch.setattr(
            q.public,
            "_coordinator",
            lambda candidate: (
                q._consume_live_public_request_v1
                if candidate is q._PUBLIC_SOURCE_KEY_V1
                else original_coordinator(candidate)
            ),
        )

        @contextmanager
        def reopen(path):
            yield self.banks[path]

        patch.setattr(q.public.journal, "_root_context", reopen)
        patch.setattr(
            q.public.transport,
            "_native_recheck_root_identity",
            lambda path: self.roots[path],
        )
        from app.trade_qualification import global_public_staged_v2 as staged

        patch.setattr(staged, "native_stamp", self.clock.stamp)
        return parent, key, request

    def staged_root(self, _base, name):
        assert name == self.state["report_id"] + "_post_g12"
        parent, _key, _request = self._public_fixture("post_g12")
        return parent.path

    async def public(self, session, stage):
        assert session is self.session
        _parent, key, request = self._public_fixture(stage)
        changes = {
            "invocation": self.state["invocation"],
            "expires_at": self.state["deadline"],
            "expires_monotonic_ns": self.state["expiry_monotonic_ns"],
        }
        if stage == "post_g12":
            changes.update(
                candidate_sha256=self.state["snapshot"].candidate_sha256,
                publication_barrier=self.state["publication"].publication_barrier,
                publication_receipt_sha256=sha(self.state["publication_json"]),
            )
        opaque, _ = request(stage, **changes)
        owner = await q.public.capture_owned_live_public_v1(opaque, key=key)
        self.native_phases.append((stage + "_captured", observed_native_stamp()))
        result = q.public._replay_owned_live_public_v1(owner, key=key)
        # Every subsequent independent source's market clock is coherent. Its
        # risk stamps are freshly derived, with the original epoch unchanged.
        declared = _declared_finite_options(result, engine_inputs(self.market))
        self.state["instrument"] = q.adapter.LiveInstrumentDataV1(
            declared["risk_inputs"].instrument, Decimal("0.01"), Decimal(1), 1, "net"
        )
        self.sources.append(owner)
        return owner, result

    def _install(self):
        p = self.patch
        # Explicit admission/SQL seams. No production API accepts these as an
        # authority source. Separate tests run genuine source guard negatives.
        p.setattr(q, "_NATIVE_GUARD", lambda: None)
        p.setattr(q, "_session", self._session)
        p.setattr(q, "_stamp", self.stamp)
        p.setattr(q, "get_settings", lambda: None)
        p.setattr(q.runtime, "_replay_live_runtime_v1", lambda *_a, **_k: self.cfg)
        p.setattr(q, "_refresh", self.refresh)
        p.setattr(q, "_control", self.control)
        p.setattr(q, "_public", self.public)
        p.setattr(q, "_private_child", self.staged_root)
        p.setattr(q.evidence, "native_stamp", lambda: self.stamp().model_dump())
        original_publish = q.evidence.storage.publish_evidence
        p.setattr(
            q.evidence.storage,
            "publish_evidence",
            lambda root, **kwargs: original_publish(
                root, clock=lambda: utc_from_ns(self.stamp().utc_ns), **kwargs
            ),
        )
        p.setattr(q.source, "_replay_owned_live_source_v1", lambda *_a, **_k: self.view)
        p.setattr(q.source, "_retained_source_identity_v1", lambda *_a, **_k: None)
        p.setattr(
            q.entry_proof.inputs,
            "account_raw_inventory_v1",
            lambda owner: sha(b"declared-account-admission-seam"),
        )
        base = engine_inputs(self.market)["policy"]
        # The instrument is bounded DATA; source metadata authenticity is an
        # intentionally inert seam here, not an assumed live observation.
        from tests.unit.test_qualification_portfolio import instrument

        spec = instrument(
            observed_at=self.market.evaluated_at, received_at=self.market.evaluated_at
        )
        self.state["instrument"] = q.adapter.LiveInstrumentDataV1(
            spec, base.prefix.tick_size, Decimal(1), 1, "net"
        )
        p.setattr(
            q.adapter,
            "live_instrument_data_v1",
            lambda *_a, **_k: self.state["instrument"],
        )
        p.setattr(
            q.adapter,
            "fixed_live_qualification_policy_v1",
            lambda *_a, **_k: self.state["policy"],
        )

        async def local(session):
            at = utc_from_ns(self.stamp().utc_ns)
            return (
                frozenset(),
                q.LiveEvidenceStamp(
                    environment="live",
                    account_id=self.view["uid"],
                    complete=True,
                    source_sha256="a" * 64,
                    observed_at=at,
                    received_at=at,
                ),
                0,
            )

        p.setattr(q, "_local", local)
        outer = self

        class Ledger:
            def __init__(self, factory, pins):
                assert factory is outer.factory and pins is outer.pins

            async def reserve_and_commit_intent_data_v1(self, value, *, owned_source):
                assert owned_source is outer.source
                outer.operations.append("durable-commit")
                await asyncio.sleep(0)
                outer.operations.append("independent-readback")
                rid, _, digest = q.exact_live_intent_document_v1(value)
                return {
                    "reservation_id": rid,
                    "intent_sha256": digest,
                    "state": "reserved",
                }

        p.setattr(q.ledger, "NativeLiveLedgerDataRepositoryV1", Ledger)

        async def submit(owner):
            outer.operations.append("physical-seam")
            q._consume_owned_live_qualification_v1(
                owner, key=q.physical._QUALIFICATION_SOURCE_KEY_V1
            )
            started = perf_counter()
            for _ in range(outer.guard_count):
                q._qualified(owner, spent=True)
                q.entry_proof.require_native_final_clock_v1(
                    owner, key=q.physical._QUALIFICATION_SOURCE_KEY_V1
                )
            outer.guard_times.append(perf_counter() - started)
            outer.native_phases.append(("last_guard", observed_native_stamp()))
            return object()

        p.setattr(q.physical, "submit_owned_live_fok_v1", submit)

    def close(self):
        for owner in self.sources:
            owner.close()
        self.stack.close()
        q._OWNED.pop(self.session, None)
        for owner in self.source_owners:
            q.source._SOURCES.pop(owner, None)
        q.financial._OWNED.pop(self.financial, None)
        q.entry_original.retire_original_math_v2(
            self.session, key=q._ENTRY_ORIGINAL_MATH_KEY_V2
        )
        proof = q.entry_proof._PENDING.pop(self.session, None)
        if proof is not None:
            q.entry_proof._PROOFS.pop(proof, None)
        for owner, record in tuple(q._QUALIFIED.items()):
            if record["session"] is self.session:
                proof = q.entry_proof._SEALED.pop(owner, None)
                if proof is not None:
                    q.entry_proof._PROOFS.pop(proof, None)
                q._QUALIFIED.pop(owner, None)
