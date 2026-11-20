"""Synthetic HMAC verification only; no runtime descriptor or signer is installed."""

import hashlib
import hmac
import sys
from types import ModuleType

import pytest

from app.okx_live import lifecycle_key_v1 as subject

INTENT_DOMAIN = b"ctcc-native-live-controlled-close-intent-v1\x00"
CLAIM_DOMAIN = b"ctcc-native-live-controlled-close-claim-v1\x00"
MATERIAL = bytes(range(32))


@pytest.fixture
def verifier(monkeypatch):
    # The additive SPEC is sealed independently. This explicit module DATA stub
    # tests the verifier contract without installing SQL or source authority.
    module = ModuleType("app.domain.native_live_controlled_close_schema_v1")
    module.INTENT_DOMAIN, module.CLAIM_DOMAIN = INTENT_DOMAIN, CLAIM_DOMAIN
    monkeypatch.setitem(sys.modules, module.__name__, module)
    owner, observed = object(), []

    def replay(candidate):
        if candidate is not owner:
            raise subject.LiveLifecycleKeyError("synthetic_original_key_required")
        observed.append(candidate)
        return {"document": {"key_hex": MATERIAL.hex()}}

    monkeypatch.setattr(subject, "_record", replay)
    return owner, observed, module


@pytest.mark.parametrize("kind", ["intent", "claim"])
def test_exact_closed_domain_retained_verifier_replays_original_key(verifier, kind):
    owner, observed, _ = verifier
    domain = INTENT_DOMAIN if kind == "intent" else CLAIM_DOMAIN
    function = getattr(
        subject, f"_verify_retained_controlled_close_{kind}_signature_v1"
    )
    raw = b'{"declared_synthetic_verification_only":true}'
    signature = hmac.digest(MATERIAL, domain + raw, hashlib.sha256).hex()
    assert function(owner, raw, signature) is True
    assert observed == [owner]
    # No raw key, signature or execution capability is returned.
    with pytest.raises(subject.LiveLifecycleKeyError):
        function(object(), raw, signature)


@pytest.mark.parametrize("kind", ["intent", "claim"])
@pytest.mark.parametrize(
    "mutation", ["domain", "raw", "key", "upper", "empty", "size", "type", "nonascii"]
)
def test_close_verifiers_refuse_cross_domain_or_changed_retained_bytes(
    verifier, kind, mutation
):
    owner, _, _ = verifier
    domain = INTENT_DOMAIN if kind == "intent" else CLAIM_DOMAIN
    function = getattr(
        subject, f"_verify_retained_controlled_close_{kind}_signature_v1"
    )
    raw = b'{"declared_synthetic_verification_only":true}'
    signing_domain = (
        (CLAIM_DOMAIN if kind == "intent" else INTENT_DOMAIN)
        if mutation == "domain"
        else domain
    )
    material = b"x" * 32 if mutation == "key" else MATERIAL
    signature = hmac.digest(material, signing_domain + raw, hashlib.sha256).hex()
    if mutation == "raw":
        raw += b" "
    elif mutation == "upper":
        signature = signature.upper()
    elif mutation == "empty":
        raw = b""
    elif mutation == "size":
        raw = b"x" * (65537 if kind == "intent" else 32769)
    elif mutation == "type":
        raw = bytearray(raw)
    elif mutation == "nonascii":
        raw = b"\xff"
    with pytest.raises(subject.LiveLifecycleKeyError):
        function(owner, raw, signature)


@pytest.mark.parametrize("kind", ["intent", "claim"])
def test_close_verifier_refuses_changed_source_domain(verifier, kind):
    owner, observed, module = verifier
    setattr(module, f"{kind.upper()}_DOMAIN", b"caller-chosen-domain\x00")
    function = getattr(
        subject, f"_verify_retained_controlled_close_{kind}_signature_v1"
    )
    with pytest.raises(subject.LiveLifecycleKeyError):
        function(owner, b"{}", "0" * 64)
    assert observed == []
