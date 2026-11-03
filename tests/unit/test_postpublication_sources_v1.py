"""Prepared synthetic IO tests; no native capture, DB, TLS or order acceptance.

Whole-body cases retain actual original replay, G12 rendering/publication,
six-file readback/computation, both original V4 issuers, raw bundle replays,
private owner MOVE and the new orchestration. The existing fixture substitutes
native HTTP/TLS/clock/DB observations. A clearly local guard substitute is
required only because those test substitutions intentionally differ from the
production source-compiled leaf pins. Production entry has no such override.
"""

import asyncio
import copy
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import FunctionType
from weakref import ref

import httpx
import pytest

from app.trade_qualification import postpublication_sources_v1 as source
from app.trade_qualification.engine import evaluate_pre_evidence
from tests.unit.qualification_engine_fixtures import engine_inputs
from tests.unit.research import test_owned_current_original_frame_v1 as native_fixture
from tests.unit.research.test_owned_public_runtime_p1 import SyntheticClock
from tests.unit.test_qualification_account_collector import Stream
from tests.unit.test_qualification_market_bridge import v2_engine_source

LATER_SIGNATURE = "U1lOVEhFVElDX09OTFlfTEFURVJfU0lHTkFUVVJFPQ=="


def _original(market, uid):
    values = engine_inputs(market)
    risk = values["risk_inputs"]
    account = risk.account.model_copy(
        update={
            "account_id": uid,
            **{
                name: getattr(risk.account, name).model_copy(update={"account_id": uid})
                for name in (
                    "balance_stamp",
                    "positions_stamp",
                    "history_stamp",
                    "reservations_stamp",
                )
            },
        }
    )
    authority = risk.authority.model_copy(
        update={"stamp": risk.authority.stamp.model_copy(update={"account_id": uid})}
    )
    values["risk_inputs"] = risk.model_copy(
        update={"account": account, "authority": authority}
    )
    run = evaluate_pre_evidence(market.market, **values)
    assert run.pre_evidence_complete, run.result.fail_codes
    return values, run


@pytest.fixture
def postpublication_original_market():
    # prefix_source owns asyncio.run; setup completes before the async test.
    return v2_engine_source("long")


def _public_http_chunk_fixture(monkeypatch, harness, damage, native):
    """Change only synthetic HTTP response bytes before their original capture."""
    original_public = harness.public
    original_client = source.runtime._new_owned_client

    def public_with_note(request):
        rows = original_public(request)
        if request.url.path.endswith("/ticker"):
            rows[0]["carried_note"] = LATER_SIGNATURE
        return rows

    harness.public = public_with_note

    def new_client(owner, role):
        client = original_client(owner, role)
        transport = client._transport
        assert type(transport) is httpx.MockTransport

        async def handler(request):
            response = await transport.handle_async_request(request)
            if not request.url.path.endswith("/ticker"):
                return response
            body = response.stream.body
            marker = LATER_SIGNATURE.encode("ascii")
            assert body.count(marker) == 1
            if damage == "later_signature_in_public_chunk_escaped":
                spelling = "".join(
                    f"\\u{ord(char):04x}" for char in LATER_SIGNATURE
                ).encode("ascii")
                body = body.replace(marker, spelling, 1)
                cut = body.index(spelling) + len(spelling) // 2 + 2
            else:
                cut = body.index(marker) + len(marker) // 2
            chunks = (body[:cut], body[cut:])
            assert all(marker not in chunk for chunk in chunks)
            # The replaced fake response is closed once; the new fake stream
            # goes through the actual collectors/journal without altered pins.
            await response.aclose()
            stream = Stream(body, chunks=chunks)
            harness.streams.append(stream)
            native.held["terminal_public_http"] = (body, chunks)
            headers = [
                (name, value)
                for name, value in response.headers.multi_items()
                if name != "content-length"
            ]
            headers.append(("content-length", str(len(body))))
            return httpx.Response(
                response.status_code,
                headers=headers,
                stream=stream,
                request=request,
                extensions=dict(response.extensions),
            )

        client._transport = httpx.MockTransport(handler)
        return client

    monkeypatch.setattr(source.runtime, "_new_owned_client", new_client)


def _assert_terminal_public_http_raw_unchanged(native, *, escaped):
    original = native.held["public_state"]["bundle_raw"]
    directory, _proof, _summary, events = source.market.replay_runtime_bundle_v4(
        original, expected_sha256=source.sha(original)
    )
    matches = [
        event["metadata"]["id"]
        for event in events
        if event["kind"] == "request"
        and event["metadata"]["endpoint"].endswith("/ticker")
    ]
    assert len(matches) == 1
    fragments = tuple(
        directory.read(f"event-{event['index']:04d}.raw", source.runtime.MAX_RAW)
        for event in events
        if event["kind"] == "chunk" and event["metadata"]["id"] == matches[0]
    )
    wire, expected_fragments = native.held["terminal_public_http"]
    assert fragments == expected_fragments and b"".join(fragments) == wire
    assert (
        source.decode(wire, source.runtime.MAX_RAW)["data"][0]["carried_note"]
        == LATER_SIGNATURE
    )
    assert all(LATER_SIGNATURE.encode() not in fragment for fragment in fragments)
    assert (LATER_SIGNATURE.encode() not in wire) is escaped
    assert native.held["public_state"]["bundle_raw"] == original
    return original, fragments


def _negative_public_journal_view(directory, events, mutation):
    """Forge ONLY a separate negative-test view; never rewrite source evidence."""
    plan_raw = directory.read("plan.json", source.runtime.MAX_RAW)
    summary = source.decode(
        directory.read("summary.json", source.runtime.MAX_RAW), source.runtime.MAX_RAW
    )
    parts = [
        [
            copy.deepcopy(event),
            None
            if event["raw_bytes"] is None
            else directory.read(
                f"event-{event['index']:04d}.raw", source.runtime.MAX_RAW
            ),
        ]
        for event in events
    ]
    request_indices = [
        index for index, (event, _raw) in enumerate(parts) if event["kind"] == "request"
    ]
    assert len(request_indices) >= 2
    first_id = parts[request_indices[0]][0]["metadata"]["id"]
    chunk_indices = [
        index
        for index, (event, _raw) in enumerate(parts)
        if event["kind"] == "chunk" and event["metadata"]["id"] == first_id
    ]
    assert len(chunk_indices) >= 2
    if mutation in {
        "missing_chunk",
        "missing_body_complete",
        "missing_response_closed",
    }:
        kind = {
            "missing_chunk": "chunk",
            "missing_body_complete": "body_complete",
            "missing_response_closed": "response_closed",
        }[mutation]
        removed = next(
            index
            for index, (event, _raw) in enumerate(parts)
            if event["kind"] == kind and event["metadata"]["id"] == first_id
        )
        parts.pop(removed)
    elif mutation in {"duplicate_request", "request_gap"}:
        request = parts[request_indices[1]][0]
        request["metadata"]["id"] = (
            first_id
            if mutation == "duplicate_request"
            else request["metadata"]["id"] + 1
        )
    elif mutation == "duplicate_event":
        pass  # Applied after normal test-view index reconstruction below.
    elif mutation == "truncated_chunk":
        event, raw = parts[chunk_indices[0]]
        event["metadata"].update(
            truncated=True, observed_bytes=len(raw) + 1, observed_sha256="0" * 64
        )
    elif mutation == "unknown_kind":
        parts[chunk_indices[0]][0]["kind"] = "unknown_future_wire_event"
    elif mutation == "malformed_complete_body":
        last = chunk_indices[-1]
        assert parts[last][1]
        parts[last][1] = parts[last][1][:-1] + b"!"
        event, raw = parts[last]
        event["metadata"]["observed_sha256"] = source.sha(raw)
        body = b"".join(parts[index][1] for index in chunk_indices)
        completed = next(
            event
            for event, _raw in parts
            if event["kind"] == "body_complete" and event["metadata"]["id"] == first_id
        )
        completed["metadata"].update(body_bytes=len(body), body_sha256=source.sha(body))
    else:
        raise AssertionError("unknown negative test mutation")
    files = {"plan.json": plan_raw}
    previous = source.sha(plan_raw)
    replayed = []
    # Reindex/rechain only these explicitly forged test views so the scanner
    # must reject the chosen missing lifecycle/data fact. This is never a
    # source repair or publication, and cannot create an owned source handle.
    for index, (event, raw) in enumerate(parts):
        event["index"] = index
        if mutation == "duplicate_event" and index == chunk_indices[0]:
            event["index"] = index - 1
        event["previous_sha256"] = previous
        event["raw_bytes"] = None if raw is None else len(raw)
        event["raw_sha256"] = None if raw is None else source.sha(raw)
        encoded = source.canonical(event)
        files[f"event-{index:04d}.json"] = encoded
        if raw is not None:
            files[f"event-{index:04d}.raw"] = raw
        previous = source.sha(encoded)
        replayed.append(event)
    summary.update(event_count=len(replayed), head_sha256=previous)
    files["summary.json"] = source.canonical(summary)
    return source.public_custody.RuntimeBundleDirectoryV4(files), tuple(replayed)


@contextmanager
def _body_fixture(monkeypatch, tmp_path, market, *, damage=None):
    """Actual new body + original native issuer bodies, synthetic native IO."""
    clock = SyntheticClock(market.evaluated_at)
    native_root = source.runtime._native_recheck_root
    original_public_setup = native_fixture.public_fixture.setup
    events = []
    mutation = "cancel" if damage == "cancel_during_account" else None
    with native_fixture.setup(
        monkeypatch, tmp_path, market, mutation=mutation
    ) as native:
        values, run = _original(
            market, native.arguments["public_account_plan"].expected_uid
        )
        roots = [
            tmp_path / name
            for name in (
                "evidence",
                "public",
                "public-private",
                "account-private",
                "account-native",
                "source-links",
            )
        ]
        for root in roots:
            root.mkdir(exist_ok=True)
        monkeypatch.setattr(source, "native_stamp", clock.stamp)
        guard = lambda: None
        monkeypatch.setattr(source, "_pins", guard)

        async def claimed(_self, claim):
            events.append("claim_commit_readback")
            await asyncio.sleep(0)
            if damage == "claim_unknown":
                raise RuntimeError("SYNTHETIC_DB_UNKNOWN")
            return replace(claim, db_claimed_at=clock.last)

        async def published(_self, claim, publication):
            events.append("publication_commit_readback")
            await asyncio.sleep(0)
            if damage == "publication_unknown":
                raise RuntimeError("SYNTHETIC_DB_UNKNOWN")
            if damage == "original_expiry":
                deadline = min(
                    run.prefix.intent.expires_at,
                    run.prefix.detection.trigger.expires_at,
                    run.result.entry_zone.expires_at,
                    run.prefix.timing.latest_valid_entry_time,
                )
                clock.count = (
                    int((deadline - market.evaluated_at).total_seconds() * 1000) + 1
                )
            return replace(publication, db_recorded_at=clock.last)

        monkeypatch.setattr(
            source.publication.G12InvocationCustodyRepository, "claim", claimed
        )
        monkeypatch.setattr(
            source.publication.G12InvocationCustodyRepository, "publication", published
        )
        synthetic_lease = native_fixture.public_fixture._synthetic_lease

        def synthetic_route_tokens(session, private):
            observation = synthetic_lease(session, private)
            route = native_fixture.route._LEASES[observation._lease]
            route["secret_tokens"] = (
                *native.lease._credentials,
                "SYNTHETIC_PUBLIC_ROUTE_SIGNATURE",
            )
            return observation

        monkeypatch.setattr(
            native_fixture.public_fixture, "_synthetic_lease", synthetic_route_tokens
        )
        original_config = native_fixture.public_fixture._config

        def config_with_note(**kwargs):
            raw = original_config(**kwargs)
            if damage in {"benign_public_note", "later_signature_in_public"}:
                document = source.decode(raw, 8192)
                document["data"][0]["carried_note"] = LATER_SIGNATURE
                return source.canonical(document)
            return raw

        monkeypatch.setattr(native_fixture.public_fixture, "_config", config_with_note)
        original_account_config = native_fixture._config

        def account_config_with_note(**kwargs):
            raw = original_account_config(**kwargs)
            if damage in {
                "benign_account_route_note",
                "later_signature_in_account_route",
            }:
                document = source.decode(raw, 8192)
                document["data"][0]["carried_note"] = LATER_SIGNATURE
                return source.canonical(document)
            return raw

        # The account preflight response is a distinct native lower IO seam;
        # neither the public route nor chain config_before is substituted.
        monkeypatch.setattr(native_fixture, "_config", account_config_with_note)

        def public_setup(*args, **kwargs):
            result = original_public_setup(*args, **kwargs)
            # Preserve the one synthetic monotonic origin across fixture phases;
            # this is not a production clock adjustment or renewed lease.
            result[0].count = clock.count + 1
            monkeypatch.setattr(source, "native_stamp", result[0].stamp)
            events.append("fresh_public")
            if damage in {
                "benign_public_chunk_note",
                "later_signature_in_public_chunk",
                "later_signature_in_public_chunk_escaped",
            }:
                _public_http_chunk_fixture(monkeypatch, result[2], damage, native)
            return result

        monkeypatch.setattr(native_fixture.public_fixture, "setup", public_setup)
        original_account = source.account.acquire_initial_demo_account_source_v4

        async def private_capture(**kwargs):
            events.append("fresh_account")
            if damage in {
                "later_signature_in_public",
                "later_signature_in_account_route",
                "later_signature_in_public_chunk",
                "later_signature_in_public_chunk_escaped",
            }:
                signed_calls = []
                original_signed = source.account.runtime.collector._signed_request

                def synthetic_later_signature(*args, **signed_kwargs):
                    # Explicit fake signing/HTTP seam only; no real HMAC or
                    # native provider claim. The earlier public source now
                    # matches a newly learned private header in this test.
                    request, signature = original_signed(*args, **signed_kwargs)
                    signed_calls.append(signature)
                    # Keep the ORIGINAL account route config signature. Only
                    # later collector signatures learn this old benign marker.
                    if (
                        damage == "later_signature_in_account_route"
                        and len(signed_calls) == 1
                    ):
                        return request, signature
                    request.headers["OK-ACCESS-SIGN"] = LATER_SIGNATURE
                    return request, LATER_SIGNATURE

                monkeypatch.setattr(
                    source.account.runtime.collector,
                    "_signed_request",
                    synthetic_later_signature,
                )
            result = await original_account(**kwargs)
            monkeypatch.setattr(source.runtime, "_native_recheck_root", native_root)
            if damage == "terminal_tokens_unknown":
                native.held["account_state"]["private_secret_tokens"] = None
            if damage == "different_lease":
                native.held["account_state"]["read_session"] = object()
            if damage == "source_pin":
                native.held["public_state"]["bundle_sha256"] = "0" * 64
            if damage == "geometry":
                values["intent"].__dict__["candidate_entry"] += 1
            if damage == "clock_leaf":
                monkeypatch.setattr(
                    source,
                    "native_stamp",
                    lambda: (_ for _ in ()).throw(
                        AssertionError("probe must not execute")
                    ),
                )

                def deny_guard():
                    raise source.PostpublicationSourceError(
                        "postpublication_source_leaf_changed"
                    )

                monkeypatch.setattr(source, "_pins", deny_guard)
            if damage == "checker_code":
                monkeypatch.setattr(
                    source._check,
                    "__code__",
                    (
                        lambda state, **kwargs: (_ for _ in ()).throw(
                            AssertionError("probe must not execute")
                        )
                    ).__code__,
                )
            return result

        monkeypatch.setattr(
            source.account, "acquire_initial_demo_account_source_v4", private_capture
        )
        kwargs = {
            "read_session": native.lease,
            "private_client": native.arguments["private_client"],
            "plan_template": native.arguments["public_account_plan"],
            "evidence_root": roots[0],
            "public_root": roots[1],
            "public_account_proof_root": roots[2],
            "account_proof_root": roots[3],
            "account_native_root": roots[4],
            "receipt_root": roots[5],
            "original_market": market.market,
            "run": run,
            "original_inputs": values,
            "market_policy": native.arguments["market_policy"],
            "session_factory": native.arguments["session_factory"],
            "custody_session_factory": native.arguments["custody_session_factory"],
            "_source_guard": guard,
        }
        attempt = source._open_acquisition(**kwargs)
        yield attempt, native, roots, events


@pytest.mark.asyncio
async def test_actual_body_real_publish_raw_replay_and_one_use_owner(
    monkeypatch, tmp_path, postpublication_original_market
):
    with _body_fixture(monkeypatch, tmp_path, postpublication_original_market) as (
        attempt,
        native,
        roots,
        events,
    ):
        owner = await attempt
        assert events[:2] == ["claim_commit_readback", "publication_commit_readback"]
        assert events[2:] == ["fresh_public", "fresh_account"]
        state = source._OWNERS[owner]
        assert native.held["public_owner"] not in source.market._CURRENT
        assert native.held["account_owner"] not in source.account._CURRENT
        assert state["public_session"] is not state["account_session"]
        assert (
            state["public_session"]._checked_read_session(state["public_session"]._plan)
            is native.lease
        )
        assert (
            state["account_session"]._checked_read_session(
                state["account_session"]._plan
            )
            is native.lease
        )
        result = source.consume_postpublication_source_operands_v1(owner)
        assert (
            result.publication_completed_at
            < source.utc_from_ns(result.barrier["utc_ns"])
            < result.expires_at
        )
        assert result.original_qualification == result.registration_site == "UNKNOWN"
        assert result.native_sources_verified and result.raw_custody_readback_verified
        assert not any(
            (
                result.account_complete,
                result.g6_evaluated,
                result.candidate_qualified,
                result.execution_recheck_complete,
                result.post_g12_eligible,
                result.atomic_risk_reserved,
                result.execution_authority,
            )
        )
        with pytest.raises(source.PostpublicationSourceError):
            source.consume_postpublication_source_operands_v1(owner)
        assert (
            state["closed"]
            and "market_state" not in state
            and "account_state" not in state
        )
        assert (
            roots[5] / "postpublication-source-links.v1.json"
        ).read_bytes() == result.receipt_json
        assert set((roots[0] / result.original_origin.candidate.report_id).iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "claim_unknown",
        "publication_unknown",
        "original_expiry",
        "source_pin",
        "geometry",
        "clock_leaf",
        "checker_code",
        "cancel_during_account",
        "terminal_tokens_unknown",
        "different_lease",
    ],
)
async def test_actual_body_unknown_or_late_failure_burns_without_source_recovery(
    monkeypatch, tmp_path, postpublication_original_market, damage
):
    with _body_fixture(
        monkeypatch, tmp_path, postpublication_original_market, damage=damage
    ) as (
        attempt,
        native,
        roots,
        events,
    ):
        held = attempt._state
        expected = (
            asyncio.CancelledError
            if damage == "cancel_during_account"
            else (source.PostpublicationSourceError, ValueError)
        )
        with pytest.raises(expected):
            await attempt
        assert held["closed"] and attempt._state is None
        assert "market_state" not in held and "account_state" not in held
        assert not (roots[5] / "postpublication-source-links.v1.json").exists()
        if damage in {"claim_unknown", "publication_unknown", "original_expiry"}:
            assert "fresh_public" not in events and "fresh_account" not in events
        if damage != "claim_unknown":
            # Accepted physical G12 files and DB/native journal observations are
            # retained; no failure path unlinks them to pretend rollback.
            assert tuple(roots[0].iterdir())
        assert all(value is not held for value in source._OWNERS.values())
        if "public_owner" in native.held:
            assert native.held["public_owner"] not in source.market._CURRENT
        if "account_owner" in native.held:
            assert native.held["account_owner"] not in source.account._CURRENT


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["close", "throw", "task_cancel"])
async def test_unstarted_attempt_terminal_cleanup_is_immediate_without_gc(
    monkeypatch, tmp_path, postpublication_original_market, terminal
):
    with _body_fixture(monkeypatch, tmp_path, postpublication_original_market) as (
        attempt,
        _native,
        roots,
        events,
    ):
        held = attempt._state
        if terminal == "close":
            attempt.close()
        elif terminal == "throw":
            with pytest.raises(RuntimeError, match="SYNTHETIC_UNSTARTED"):
                attempt.throw(RuntimeError("SYNTHETIC_UNSTARTED"))
        else:
            task = asyncio.create_task(attempt)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert held["closed"] and attempt._state is None and not events
        assert not tuple(roots[0].iterdir()) and not tuple(roots[5].iterdir())
        with pytest.raises((RuntimeError, StopIteration)):
            attempt.send(None)


@pytest.mark.asyncio
async def test_success_owner_abandonment_releases_native_caps_without_gc(
    monkeypatch, tmp_path, postpublication_original_market
):
    with _body_fixture(monkeypatch, tmp_path, postpublication_original_market) as (
        attempt,
        native,
        roots,
        _events,
    ):
        owner = await attempt
        state = source._OWNERS[owner]
        route_claim = state["market_state"]["v4_route_claim"]
        owner_ref = ref(owner)
        detached = dict(state)
        receipt = state["receipt_json"]
        del owner
        assert owner_ref() is None
        assert (
            state["closed"]
            and "market_state" not in state
            and "account_state" not in state
        )
        assert route_claim not in source.market.operational._OWNED_ROUTES
        assert detached["receipt_json"] == receipt
        assert (
            roots[5] / "postpublication-source-links.v1.json"
        ).read_bytes() == receipt
        assert native.held["public_owner"] not in source.market._CURRENT


@pytest.mark.asyncio
async def test_actual_body_benign_unknown_public_note_preserves_exact_source(
    monkeypatch, tmp_path, postpublication_original_market
):
    with _body_fixture(
        monkeypatch,
        tmp_path,
        postpublication_original_market,
        damage="benign_public_note",
    ) as (
        attempt,
        native,
        _roots,
        _events,
    ):
        owner = await attempt
        state = source._OWNERS[owner]
        route = source.market.operational._owned_route_state_v4(
            state["market_state"]["v4_route_claim"],
            invocation=state["market_state"]["invocation"],
            owner_task=asyncio.current_task(),
        )
        assert LATER_SIGNATURE.encode() in route["raw"]
        assert LATER_SIGNATURE not in state["terminal_tokens"]
        before = native.held["public_state"]["bundle_raw"]
        result = source.consume_postpublication_source_operands_v1(owner)
        assert native.held["public_state"]["bundle_raw"] == before
        assert (
            result.original_qualification == "UNKNOWN"
            and not result.execution_authority
        )


@pytest.mark.asyncio
async def test_actual_body_new_known_signature_in_earlier_public_raw_denies_without_rewrite(
    monkeypatch, tmp_path, postpublication_original_market
):
    original_scan = source._secret_complete
    observations = []

    def retained_scan(state, directory, document, recorded, events):
        raw = state["market_state"]["bundle_raw"]
        assert LATER_SIGNATURE.encode() not in raw  # nested retained config hex
        try:
            return original_scan(state, directory, document, recorded, events)
        except source.account.runtime.collector.AccountCollectionError:
            observations.append((raw, "terminal_known_secret_rejected"))
            raise

    with _body_fixture(
        monkeypatch,
        tmp_path,
        postpublication_original_market,
        damage="later_signature_in_public",
    ) as (
        attempt,
        native,
        roots,
        _events,
    ):
        monkeypatch.setattr(source, "_secret_complete", retained_scan)
        with pytest.raises(source.PostpublicationSourceError):
            await attempt
        assert (
            len(observations) == 1
            and observations[0][1] == "terminal_known_secret_rejected"
        )
        assert native.held["public_state"]["bundle_raw"] == observations[0][0]
        assert not (roots[5] / "postpublication-source-links.v1.json").exists()
        assert tuple(roots[0].iterdir()) and native.held["account_native_files"]


@pytest.mark.parametrize("axis", ["utc_ns", "monotonic_ns"])
@pytest.mark.parametrize("delta", [-1, 0])
def test_strict_causal_barrier_rejects_equal_or_earlier_request(axis, delta):
    barrier = {"utc_ns": 1_000_000_000, "monotonic_ns": 100_000_000}
    stamp = {key: value + 1 for key, value in barrier.items()}
    source._after(barrier, stamp)
    stamp[axis] = barrier[axis] + delta
    with pytest.raises(ValueError):
        source._after(barrier, stamp)


@pytest.mark.parametrize(
    "leaf",
    [
        "canonical",
        "sha",
        "validate_stamps",
        "_clock_after",
        "_lineage",
        "_fresh_session",
        "_secret_public_journal",
    ],
)
def test_saved_guard_rejects_replaced_critical_leaf_before_probe(monkeypatch, leaf):
    saved = source._pins
    saved()
    called = []
    monkeypatch.setattr(source, leaf, lambda *_args, **_kwargs: called.append(leaf))
    with pytest.raises(source.PostpublicationSourceError, match="source_leaf_changed"):
        saved()
    assert not called


@pytest.mark.parametrize("mutation", ["code", "kwdefaults"])
def test_saved_guard_rejects_same_object_code_or_keyword_mutation(
    monkeypatch, mutation
):
    saved = source._pins
    saved()
    original = source._check
    if mutation == "code":
        monkeypatch.setattr(
            original, "__code__", (lambda state, **kwargs: None).__code__
        )
    else:
        monkeypatch.setitem(original.__kwdefaults__, "publication_required", True)
    with pytest.raises(source.PostpublicationSourceError, match="source_leaf_changed"):
        saved()


def test_source_compilation_refuses_preexisting_same_filename_forged_leaf():
    original = source._after
    path = Path(original.__code__.co_filename)
    source._source_code(original, path.read_bytes(), original.__code__.co_filename)
    compiled = compile(
        "def _after(barrier, stamp):\n    return None\n",
        original.__code__.co_filename,
        "exec",
        dont_inherit=True,
    )
    forged = FunctionType(
        next(value for value in compiled.co_consts if hasattr(value, "co_name")),
        vars(source),
    )
    with pytest.raises(source.PostpublicationSourceError, match="source_code_unbound"):
        source._source_code(forged, path.read_bytes(), original.__code__.co_filename)


def test_saved_guard_rejects_in_place_terminal_scanner_code_change(monkeypatch):
    saved = source._pins
    saved()
    scanner = source._secret_complete
    monkeypatch.setattr(
        scanner,
        "__code__",
        (lambda state, directory, document, recorded: None).__code__,
    )
    with pytest.raises(source.PostpublicationSourceError, match="source_leaf_changed"):
        saved()


def test_private_open_refuses_foreign_guard_before_probe_or_input_callbacks():
    invoked = []
    kwargs = {
        name: object()
        for name in (
            "read_session",
            "private_client",
            "plan_template",
            "evidence_root",
            "public_root",
            "public_account_proof_root",
            "account_proof_root",
            "account_native_root",
            "receipt_root",
            "original_market",
            "run",
            "original_inputs",
            "market_policy",
            "session_factory",
            "custody_session_factory",
        )
    }
    with pytest.raises(source.PostpublicationSourceError, match="source_leaf_changed"):
        source._open_acquisition(**kwargs, _source_guard=lambda: invoked.append(True))
    assert not invoked


@pytest.mark.parametrize("boundary", ["entry", "check"])
def test_guard_closure_cell_mutation_is_refused_before_invoking_modified_guard(
    monkeypatch, boundary
):
    guard = source._pins
    guard()
    cells = tuple(cell.cell_contents for cell in guard.__closure__)
    state = {"guard": guard, "guard_code": guard.__code__, "guard_cells": cells}
    record_cell = dict(zip(guard.__code__.co_freevars, guard.__closure__, strict=True))[
        "records"
    ]
    monkeypatch.setattr(record_cell, "cell_contents", ())
    with pytest.raises(source.PostpublicationSourceError, match="source_leaf_changed"):
        if boundary == "check":
            source._check(state)  # must refuse before context/clock/source access
        else:
            source.publish_acquire_postpublication_sources_v1(
                **{
                    name: object()
                    for name in (
                        "read_session",
                        "private_client",
                        "plan_template",
                        "evidence_root",
                        "public_root",
                        "public_account_proof_root",
                        "account_proof_root",
                        "account_native_root",
                        "receipt_root",
                        "original_market",
                        "run",
                        "original_inputs",
                        "market_policy",
                        "session_factory",
                        "custody_session_factory",
                    )
                }
            )


@pytest.mark.parametrize("name", ["globals", "dict", "any"])
def test_saved_guard_refuses_builtin_shadow_without_calling_probe(monkeypatch, name):
    saved = source._pins
    saved()
    invoked = []
    monkeypatch.setattr(
        source, name, lambda *_args: invoked.append(name), raising=False
    )
    with pytest.raises(source.PostpublicationSourceError, match="source_leaf_changed"):
        saved()
    assert not invoked


@pytest.mark.parametrize("kind", ["construct", "copy", "saved_bundle", "dto"])
def test_no_saved_receipt_or_public_fact_can_construct_or_consume_native_owner(kind):
    if kind == "construct":
        with pytest.raises(source.PostpublicationSourceError):
            source.OwnedPostpublicationSourcesV1()
    elif kind == "copy":
        unregistered = object.__new__(source.OwnedPostpublicationSourcesV1)
        with pytest.raises(source.PostpublicationSourceError):
            copy.copy(unregistered)
    else:
        value = (
            b"{}"
            if kind == "saved_bundle"
            else {"native_sources_verified": True, "execution_authority": True}
        )
        with pytest.raises(source.PostpublicationSourceError):
            source.consume_postpublication_source_operands_v1(value)


@pytest.mark.asyncio
async def test_actual_body_benign_account_route_note_preserves_original_raw(
    monkeypatch, tmp_path, postpublication_original_market
):
    with _body_fixture(
        monkeypatch,
        tmp_path,
        postpublication_original_market,
        damage="benign_account_route_note",
    ) as (
        attempt,
        native,
        _roots,
        _events,
    ):
        owner = await attempt
        state = source._OWNERS[owner]
        original = native.held["account_state"]["bundle_raw"]
        document = source.decode(original, source.custody.MAX_BUNDLE_BYTES)
        raw = source.account._unhex(document["route_proof"]["config_raw_hex"], 8192)
        assert (
            LATER_SIGNATURE.encode() in raw and LATER_SIGNATURE.encode() not in original
        )
        assert LATER_SIGNATURE not in state["terminal_tokens"]
        source.account.replay_current_account_bundle_v4(
            original, expected_sha256=source.sha(original)
        )
        result = source.consume_postpublication_source_operands_v1(owner)
        assert native.held["account_state"]["bundle_raw"] == original
        assert (
            result.original_qualification == "UNKNOWN"
            and not result.execution_authority
        )


@pytest.mark.asyncio
async def test_actual_body_later_signature_in_earlier_account_route_denies_without_rewrite(
    monkeypatch, tmp_path, postpublication_original_market
):
    original_scan = source._secret_complete
    observations = []

    def retained_scan(state, directory, document, recorded, events):
        original = state["account_state"]["bundle_raw"]
        raw = source.account._unhex(document["route_proof"]["config_raw_hex"], 8192)
        assert (
            LATER_SIGNATURE.encode() in raw and LATER_SIGNATURE.encode() not in original
        )
        # The exact original bundle replays first. The marker was benign before
        # this NEW collector learned it as a signature; no source is rewritten.
        source.account.replay_current_account_bundle_v4(
            original, expected_sha256=source.sha(original)
        )
        tokens = state["account_state"]["private_secret_tokens"]
        assert LATER_SIGNATURE in tokens
        source.account.runtime._secret_checked(
            raw, tuple(token for token in tokens if token != LATER_SIGNATURE)
        )
        try:
            return original_scan(state, directory, document, recorded, events)
        except source.account.runtime.collector.AccountCollectionError:
            observations.append((original, raw))
            raise

    with _body_fixture(
        monkeypatch,
        tmp_path,
        postpublication_original_market,
        damage="later_signature_in_account_route",
    ) as (attempt, native, roots, _events):
        monkeypatch.setattr(source, "_secret_complete", retained_scan)
        with pytest.raises(source.PostpublicationSourceError):
            await attempt
        assert len(observations) == 1
        assert native.held["account_state"]["bundle_raw"] == observations[0][0]
        assert not (roots[5] / "postpublication-source-links.v1.json").exists()
        assert tuple(roots[0].iterdir()) and native.held["account_native_files"]


@pytest.mark.parametrize("namespace_change", ["foreign_globals", "foreign_builtins"])
def test_source_compilation_refuses_same_code_foreign_namespace_before_probe(
    monkeypatch, namespace_change
):
    original = source.account.runtime._secret_checked
    path = Path(original.__code__.co_filename)
    raw = path.read_bytes()
    source._source_code(original, raw, original.__code__.co_filename)
    invoked = []

    class ForeignCollector:
        def _no_secrets(self, *_args):
            invoked.append(True)

        def _no_secret_json(self, *_args):
            invoked.append(True)

    with monkeypatch.context() as changed:
        if namespace_change == "foreign_globals":
            namespace = dict(original.__globals__)
            namespace["collector"] = ForeignCollector()
        else:
            namespace = original.__globals__
            changed.setitem(namespace, "__builtins__", dict(original.__builtins__))
        forged = FunctionType(
            original.__code__,
            namespace,
            original.__name__,
            original.__defaults__,
            original.__closure__,
        )
        forged.__kwdefaults__ = original.__kwdefaults__
        assert forged.__code__ is original.__code__
        assert forged.__code__.co_filename == original.__code__.co_filename
        with pytest.raises(
            source.PostpublicationSourceError, match="source_namespace_unbound"
        ):
            source._source_code(forged, raw, original.__code__.co_filename)
    assert not invoked
    source._source_code(original, raw, original.__code__.co_filename)


def test_saved_guard_refuses_external_leaf_builtin_shadow_before_probe(monkeypatch):
    guard = source._pins
    guard()
    invoked = []
    with monkeypatch.context() as changed:
        changed.setattr(
            source.account.runtime.collector,
            "any",
            lambda *_args: invoked.append(True),
            raising=False,
        )
        with pytest.raises(
            source.PostpublicationSourceError, match="source_leaf_changed"
        ):
            guard()
    assert not invoked
    guard()


def test_source_code_comparison_ignores_reference_graph_without_ignoring_fields():
    original = source.market.public._policy_copy
    path = Path(original.__code__.co_filename)
    raw = path.read_bytes()
    held_constants = original.__code__.co_consts
    source._source_code(original, raw, original.__code__.co_filename)
    assert held_constants is original.__code__.co_consts
    source._source_code(original, raw, original.__code__.co_filename)


@pytest.mark.parametrize("field", ["co_filename", "co_firstlineno", "co_consts"])
def test_source_code_comparison_refuses_exact_location_or_constant_change(field):
    original = source._after
    path = Path(original.__code__.co_filename)
    raw = path.read_bytes()
    changes = {
        "co_filename": original.__code__.co_filename + ".forged",
        "co_firstlineno": original.__code__.co_firstlineno + 1,
        "co_consts": (*original.__code__.co_consts, "unreviewed constant"),
    }
    forged = FunctionType(
        original.__code__.replace(**{field: changes[field]}), vars(source)
    )
    with pytest.raises(source.PostpublicationSourceError, match="source_code_unbound"):
        source._source_code(forged, raw, original.__code__.co_filename)


def test_pinned_plan_wrapper_original_and_guard_are_both_bound():
    wrapper = source.capture.plan_sha256
    raw = Path(wrapper.__code__.co_filename).read_bytes()
    record = source._source_wrapped_leaf(wrapper, raw, wrapper.__code__.co_filename)
    assert record[0] is wrapper and record[1] == "__wrapped__"
    assert record[2] is wrapper.__wrapped__ is wrapper.__closure__[0].cell_contents
    source._pins()


@pytest.mark.parametrize("mutation", ["cell", "code", "defaults", "metadata"])
def test_saved_guard_refuses_pinned_wrapper_original_mutation_before_probe(
    monkeypatch, mutation
):
    guard = source._pins
    guard()
    wrapper = source.capture.plan_sha256
    original = wrapper.__wrapped__
    called = []

    def replacement(*_args, **_kwargs):
        called.append(True)

    if mutation == "cell":
        monkeypatch.setattr(wrapper.__closure__[0], "cell_contents", replacement)
    elif mutation == "code":
        monkeypatch.setattr(original, "__code__", (lambda plan: None).__code__)
    elif mutation == "defaults":
        monkeypatch.setattr(original, "__defaults__", (object(),))
    else:
        monkeypatch.setattr(wrapper, "__wrapped__", replacement)
    with pytest.raises(source.PostpublicationSourceError, match="source_leaf_changed"):
        guard()
    assert called == []


def test_bootstrap_wrapper_binding_refuses_foreign_original_before_probe(
    monkeypatch,
):
    wrapper = source.capture.plan_sha256
    raw = Path(wrapper.__code__.co_filename).read_bytes()
    original = wrapper.__wrapped__
    foreign = FunctionType(original.__code__, dict(original.__globals__))
    with monkeypatch.context() as changed:
        changed.setattr(wrapper.__closure__[0], "cell_contents", foreign)
        changed.setattr(wrapper, "__wrapped__", foreign)
        with pytest.raises(
            source.PostpublicationSourceError, match="source_namespace_unbound"
        ):
            source._source_wrapped_leaf(wrapper, raw, wrapper.__code__.co_filename)


@pytest.mark.asyncio
async def test_actual_body_benign_split_public_http_note_preserves_all_raw_fragments(
    monkeypatch, tmp_path, postpublication_original_market
):
    with _body_fixture(
        monkeypatch,
        tmp_path,
        postpublication_original_market,
        damage="benign_public_chunk_note",
    ) as (attempt, native, roots, _events):
        owner = await attempt
        state = source._OWNERS[owner]
        assert LATER_SIGNATURE not in state["terminal_tokens"]
        original, fragments = _assert_terminal_public_http_raw_unchanged(
            native, escaped=False
        )
        assert len(fragments) == 2
        result = source.consume_postpublication_source_operands_v1(owner)
        assert not result.execution_authority and not result.account_complete
        assert native.held["public_state"]["bundle_raw"] == original
        assert (
            roots[5] / "postpublication-source-links.v1.json"
        ).read_bytes() == result.receipt_json


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["plain", "escaped"])
async def test_actual_body_later_secret_in_split_public_http_body_is_rejected(
    monkeypatch, tmp_path, postpublication_original_market, spelling
):
    original_scan = source._secret_complete
    observed = []

    def retained_scan(state, directory, document, recorded, events):
        assert LATER_SIGNATURE in state["account_state"]["private_secret_tokens"]
        try:
            return original_scan(state, directory, document, recorded, events)
        except source.account.runtime.collector.AccountCollectionError:
            observed.append("terminal_full_union_secret_rejected")
            raise

    damage = (
        "later_signature_in_public_chunk_escaped"
        if spelling == "escaped"
        else "later_signature_in_public_chunk"
    )
    with _body_fixture(
        monkeypatch, tmp_path, postpublication_original_market, damage=damage
    ) as (attempt, native, roots, _events):
        monkeypatch.setattr(source, "_secret_complete", retained_scan)
        with pytest.raises(source.PostpublicationSourceError):
            await attempt
        assert observed == ["terminal_full_union_secret_rejected"]
        _assert_terminal_public_http_raw_unchanged(
            native, escaped=spelling == "escaped"
        )
        assert tuple(roots[0].iterdir()) and native.held["account_native_files"]
        assert not (roots[5] / "postpublication-source-links.v1.json").exists()


@pytest.mark.parametrize(
    "target",
    [
        "directory_read",
        "directory_names",
        "journal_semantics",
        "account_collector_alias",
        "public_error_alias",
    ],
)
def test_public_terminal_secret_dependencies_refuse_replacement_before_probe(
    monkeypatch, target
):
    guard = source._pins
    guard()
    namespace, name = {
        "directory_read": (source.public_custody.RuntimeBundleDirectoryV4, "read"),
        "directory_names": (source.public_custody.RuntimeBundleDirectoryV4, "names"),
        "journal_semantics": (source.public_custody.journal, "_replay_semantics"),
        "account_collector_alias": (source.account.runtime, "collector"),
        "public_error_alias": (source.public_custody, "PublicReceiptError"),
    }[target]
    called = []
    monkeypatch.setattr(namespace, name, lambda *_args, **_kwargs: called.append(True))
    with pytest.raises(source.PostpublicationSourceError, match="source_leaf_changed"):
        guard()
    assert called == []


@pytest.mark.asyncio
async def test_actual_body_terminal_scanner_refuses_all_incomplete_negative_views(
    monkeypatch, tmp_path, postpublication_original_market
):
    with _body_fixture(monkeypatch, tmp_path, postpublication_original_market) as (
        attempt,
        native,
        _roots,
        _events,
    ):
        owner = await attempt
        state = source._OWNERS[owner]
        original = native.held["public_state"]["bundle_raw"]
        original_pin = source.sha(original)
        directory, _proof, _summary, events = source.market.replay_runtime_bundle_v4(
            original, expected_sha256=original_pin
        )
        cases = [
            "missing_chunk",
            "duplicate_request",
            "request_gap",
            "duplicate_event",
            "truncated_chunk",
            "missing_body_complete",
            "missing_response_closed",
            "unknown_kind",
            "malformed_complete_body",
        ]
        rejected = []
        for mutation in cases:
            view, changed_events = _negative_public_journal_view(
                directory, events, mutation
            )
            with pytest.raises((ValueError, KeyError)):
                source._secret_public_journal(
                    view, changed_events, state["terminal_tokens"]
                )
            rejected.append(mutation)
            assert native.held["public_state"]["bundle_raw"] == original
            assert source.sha(original) == original_pin
        assert rejected == cases
        # The actual unmodified owner still consumes normally after all nine
        # forged audit views refused. The views themselves never gain authority.
        result = source.consume_postpublication_source_operands_v1(owner)
        assert not result.execution_authority and not result.account_complete
        assert native.held["public_state"]["bundle_raw"] == original
