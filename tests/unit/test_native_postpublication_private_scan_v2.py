"""Negative private scanner purposes only; no source/route owner is fabricated."""

import asyncio
import sys
from types import ModuleType

import pytest

from app.trade_qualification import native_postpublication_recheck_v2 as subject


def _scanner_key_fixture(monkeypatch):
    from app import trade_qualification

    source = ModuleType("app.trade_qualification.native_controlled_account_source_v6")
    source._SCAN_KEY_V6 = object()
    monkeypatch.setitem(sys.modules, source.__name__, source)
    monkeypatch.setattr(
        trade_qualification,
        "native_controlled_account_source_v6",
        source,
        raising=False,
    )
    return source._SCAN_KEY_V6


def test_caller_key_cannot_read_route_tokens(monkeypatch):
    _scanner_key_fixture(monkeypatch)
    with pytest.raises(subject.NativeR7Error, match="private_public_scanner_required"):
        subject._postpublication_public_scan_tokens_v2(object(), key=object())


@pytest.mark.asyncio
async def test_unknown_generation_never_reaches_public_route(monkeypatch):
    key = _scanner_key_fixture(monkeypatch)
    monkeypatch.setattr(subject, "_PUBLIC_SCAN_HOLDS", {})
    with pytest.raises(subject.NativeR7Error, match="exact_public_scan_owner_required"):
        subject._postpublication_public_scan_tokens_v2(object(), key=key)


@pytest.mark.asyncio
async def test_changed_account_binding_denies_before_route_replay(monkeypatch):
    key = _scanner_key_fixture(monkeypatch)
    generation, registration = object(), object()
    public, claim = {}, object()
    public.update(v4_route_claim=claim, bundle_sha256="0" * 64)
    state = {
        "task": asyncio.current_task(),
        "public_scan_source_generation": generation,
        "public_scan_binding": registration,
        "account_binding": object(),
        "public_scan_account_binding": object(),
        "public_state": public,
        "public_scan_source_pin": (public, claim, "0" * 64),
    }
    monkeypatch.setattr(subject, "_PUBLIC_SCAN_HOLDS", {registration: state})
    # Isolate the negative identity branch; no passing source/route is supplied.
    monkeypatch.setattr(
        subject._native_guard, "check_saved_native_guard_v1", lambda state: None
    )
    called = []
    monkeypatch.setattr(
        subject.market,
        "_replay_native_postpublication_v2",
        lambda *args, **kw: called.append(True),
    )
    with pytest.raises(subject.NativeR7Error, match="public_scan_binding_changed"):
        subject._postpublication_public_scan_tokens_v2(generation, key=key)
    assert called == []
