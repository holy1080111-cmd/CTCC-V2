"""One owned invocation from original native G11 to durable intent handoff.

Only genuine preexisting native source owners enter. No raw market/account,
saved receipt, PASS, clock, collector, publisher, factory or order callback is
accepted. Legacy final write guards remain unchanged. Final native transport
requires its separate genuine same-task source and physical-write boundary.
"""

import sys as _native_sys
from collections.abc import Coroutine
from dataclasses import dataclass
from pathlib import Path

from app.trade_qualification import native_account_risk_assembly_v1 as account
from app.trade_qualification import native_continuation_guard_v1 as _native_guard
from app.trade_qualification import native_g12_publication_v2 as publication
from app.trade_qualification import native_ledger_runtime_v2 as ledger
from app.trade_qualification import native_original_local_current55_v2 as original55
from app.trade_qualification import native_postpublication_recheck_v2 as recheck
from app.trade_qualification import owned_native_candidate_v1 as candidate


@dataclass(frozen=True, slots=True)
class NativeInvocationRootsV2:
    evidence_root: Path
    postpublication: recheck.NativePostpublicationRootsV2


@dataclass(frozen=True, slots=True)
class NativeCurrent55InvocationRootsV2:
    original_public_root: Path
    original_public_account_proof_root: Path
    continuation: NativeInvocationRootsV2


def _release(state):
    pending = state.pop("pending", None)
    try:
        if pending is not None:
            pending.close()
    finally:
        owner = state.pop("owner", None)
        if owner is not None:
            owner.close()


class _Attempt(Coroutine):
    def __init__(self, state):
        self.state, self.body = state, _run(state)

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        try:
            return self.body.send(value)
        except BaseException:
            _release(self.state)
            raise

    def throw(self, *args):
        try:
            return self.body.throw(*args)
        except BaseException:
            _release(self.state)
            raise

    def close(self):
        try:
            self.body.close()
        finally:
            _release(self.state)


def complete_owned_native_invocation_v2(
    original_local_owner,
    original_issued_account_owner,
    *,
    requested_contracts,
    requested_leverage,
    roots,
):
    """MOVE once; leverage is a ceiling for source-derived G11 selection."""
    if type(roots) is not NativeInvocationRootsV2:
        raise ValueError("native_invocation_exact_roots_required")
    original = candidate.combine_owned_original_g11_v1(
        original_local_owner,
        original_issued_account_owner,
        requested_contracts=requested_contracts,
        requested_leverage=requested_leverage,
    )
    state = {"owner": original, "roots": roots}
    try:
        state["pending"] = publication.publish_owned_native_candidate_v2(
            original, evidence_root=roots.evidence_root
        )
        state["owner"] = None  # Source ownership moved to the actual publisher.
        return _Attempt(state)
    except BaseException:
        _release(state)
        raise


async def _run(state):
    try:
        state["owner"] = await state["pending"]
        state["pending"] = None
        state["pending"] = recheck.capture_recheck_owned_native_v2(
            state["owner"], roots=state["roots"].postpublication
        )
        state["owner"] = None
        state["owner"] = await state["pending"]
        state["pending"] = None
        state["pending"] = ledger.begin_owned(state["owner"])
        state["owner"] = None
        result = await state["pending"]
        state["pending"] = None
        return result
    finally:
        _release(state)


def _release_current55(state):
    pending = state.pop("pending", None)
    try:
        if pending is not None:
            pending.close()
    finally:
        local_owner = state.pop("local_owner", None)
        try:
            if local_owner is not None:
                local_owner.close()
        finally:
            issued_owner = state.pop("issued_owner", None)
            if issued_owner is not None:
                account.discard_issued_account_owner_v1(
                    issued_owner, key=original55._SOURCE_KEY_V2
                )


class _Current55Attempt(Coroutine):
    def __init__(self, state):
        self.state, self.body = state, _run_current55(state)

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        try:
            return self.body.send(value)
        except BaseException:
            _release_current55(self.state)
            raise

    def throw(self, *args):
        try:
            return self.body.throw(*args)
        except BaseException:
            _release_current55(self.state)
            raise

    def close(self):
        try:
            self.body.close()
        finally:
            _release_current55(self.state)


def complete_owned_native_current55_v2(
    issued_owner,
    *,
    instrument_id,
    strategy,
    market_policy,
    requested_contracts,
    requested_leverage,
    roots,
):
    """One genuine Armed55 source into NEW original→G12→NEW R7→durable intent."""
    if (
        type(roots) is not NativeCurrent55InvocationRootsV2
        or type(roots.continuation) is not NativeInvocationRootsV2
        or type(roots.continuation.postpublication)
        is not recheck.NativePostpublicationRootsV2
        or type(requested_contracts) is not candidate.Decimal
        or not requested_contracts.is_finite()
        or not candidate.Decimal(0) < requested_contracts < candidate.Decimal("1e20")
        or len(requested_contracts.as_tuple().digits) > 40
        or requested_contracts.as_tuple().exponent < -20
        or type(requested_leverage) is not int
        or not 1 <= requested_leverage <= 20
    ):
        raise ValueError("native_current55_invocation_configuration_invalid")
    post = roots.continuation.postpublication
    all_roots = (
        roots.original_public_root,
        roots.original_public_account_proof_root,
        roots.continuation.evidence_root,
        post.public_root,
        post.public_account_proof_root,
        post.account_roots.history_raw_root,
        post.account_roots.history_companion_root,
        post.account_roots.account_proof_root,
        post.account_roots.account_native_root,
    )
    if (
        any(
            type(root) is not type(Path()) or not root.is_absolute()
            for root in all_roots
        )
        or len(set(all_roots)) != 9
        or any(a in b.parents for a in all_roots for b in all_roots if a != b)
    ):
        raise ValueError("native_current55_invocation_fresh_disjoint_roots_required")
    state = {
        "issued_owner": issued_owner,
        "roots": roots,
        "requested_contracts": requested_contracts,
        "requested_leverage": requested_leverage,
    }
    try:
        state["pending"] = original55.capture_owned_original_current55_local_v2(
            issued_owner,
            instrument_id=instrument_id,
            strategy=strategy,
            market_policy=market_policy,
            public_root=roots.original_public_root,
            public_account_proof_root=roots.original_public_account_proof_root,
        )
        return _Current55Attempt(state)
    except BaseException:
        _release_current55(state)
        raise


async def _run_current55(state):
    try:
        state["local_owner"] = await state["pending"]
        state["pending"] = None
        state["pending"] = complete_owned_native_invocation_v2(
            state["local_owner"],
            state["issued_owner"],
            requested_contracts=state["requested_contracts"],
            requested_leverage=state["requested_leverage"],
            roots=state["roots"].continuation,
        )
        state["local_owner"], state["issued_owner"] = None, None
        result = await state["pending"]
        state["pending"] = None
        return result
    finally:
        _release_current55(state)


_NATIVE_GUARD = _native_guard.install_native_guards_v1(
    _native_sys.modules[__name__],
    ("complete_owned_native_invocation_v2", "complete_owned_native_current55_v2"),
    dependencies=(candidate, publication, recheck, ledger, original55, account),
)
