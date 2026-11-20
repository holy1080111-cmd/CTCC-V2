"""Native held-scope financial seals; no free signing API or exchange writes.

Only the original configured Live GET lease supplies purpose-derived key
material. Keys never leave this module. Credential/site/database continuity is
required on restart; rotation refuses old prefixes and never resets them.
"""

import hashlib
import hmac

from app.domain import native_live_financial_epoch_schema_v1 as spec
from app.domain.source_primitives import canonical, decode, sha
from app.okx_live import financial_epoch_math_v1 as math
from app.okx_live import financial_rows_v1 as rows_source
from app.okx_live import trusted_source_bridge_v1 as source

_SOURCE_KEY = object()
_SCHEMA = "ctcc.native_live.financial_prefix_auth.v1"


def _need(condition):
    if condition is not True:
        raise ValueError(
            "native_live_financial_prefix_authentication_refused"
        ) from None


def _native_operation_v1(source_owner, *, key, operation):
    scope = _held_scope_v1(source_owner, key)
    _need(operation in ("verify", "genesis", "sample"))
    pins = scope["pins"]
    view = source._replay_owned_live_source_v1(source_owner, key=source._SOURCE_KEY)
    state = source._SOURCES[source_owner]
    state["lease"].verify_current()
    pin = state["lease"]._pin
    identity = canonical(
        [
            _SCHEMA,
            "live",
            view["uid"],
            view["main_uid"],
            pin.origin,
            spec.POLICY_SHA256,
            pins.database_oid,
            pins.owner_oid,
            pin.credentials[0],
            pin.credentials[2],
        ]
    )
    material = hmac.new(
        pin.credentials[1].encode("utf8"), identity, hashlib.sha256
    ).digest()
    if operation == "verify":
        return _verify_scope_data_v1(scope, material, view)
    if operation == "genesis":
        _need(
            scope["epoch"] is None
            and scope["prefix"] == ()
            and scope.get("genesis_issued") is not True
        )
    else:
        _need(scope.get("sample_issued") is not True)
        _verify_scope_data_v1(scope, material, view)
    rows = rows_source.read_owned_live_financial_rows_data_v1(source_owner)
    _need(rows.receipt_json == scope["rows"].receipt_json)
    document = decode(
        math.epoch_document(rows)
        if operation == "genesis"
        else math.sample_document(
            rows, original_epoch_json=scope["epoch"], original_prefix=scope["prefix"]
        ),
        maximum=2097152,
    )
    document.update(
        native_source_receipt_json=view["receipt_json"].decode("ascii"),
        native_financial_tail_json=view["financial_tail_json"].decode("ascii"),
    )
    raw = _seal_data_v1(
        material, "epoch" if operation == "genesis" else "sample", document
    )
    _need(len(raw) <= (65536 if operation == "genesis" else 2097152))
    scope["genesis_issued" if operation == "genesis" else "sample_issued"] = True
    return raw


def _key_id(key):
    return hmac.new(key, b"ctcc-live-financial-key-id-v1", hashlib.sha256).hexdigest()


def _tag(key, purpose, raw):
    _need(purpose in ("epoch", "sample") and type(raw) is bytes)
    return hmac.new(
        key,
        b"ctcc-live-financial-seal-v1\x00" + purpose.encode("ascii") + b"\x00" + raw,
        hashlib.sha256,
    ).hexdigest()


def _seal_data_v1(key, purpose, document):
    """Private cryptographic primitive, not a native producer or capability."""
    _need(type(document) is dict and "native_authentication" not in document)
    raw = canonical(document)
    return canonical(
        {
            **document,
            "native_authentication": {
                "schema": _SCHEMA,
                "key_id": _key_id(key),
                "tag": _tag(key, purpose, raw),
            },
        }
    )


def _verify_data_v1(key, purpose, raw):
    document = decode(raw, maximum=2097152)
    _need(type(document) is dict and canonical(document) == raw)
    unsigned = dict(document)
    authentication = unsigned.pop("native_authentication", None)
    _need(
        type(authentication) is dict
        and set(authentication) == {"schema", "key_id", "tag"}
    )
    _need(
        authentication["schema"] == _SCHEMA
        and type(authentication["key_id"]) is str
        and type(authentication["tag"]) is str
    )
    _need(
        hmac.compare_digest(authentication["key_id"], _key_id(key))
        and hmac.compare_digest(
            authentication["tag"], _tag(key, purpose, canonical(unsigned))
        )
    )
    return unsigned


def _checked_original_source_documents_v1(document, view):
    bridge_raw = document["native_source_receipt_json"].encode("ascii")
    tail_raw = document["native_financial_tail_json"].encode("ascii")
    bridge = decode(bridge_raw, maximum=65536)
    tail = decode(tail_raw, maximum=16384)
    expected_source = document.get(
        "source_revision_sha256", document.get("genesis_source_revision_sha256")
    )
    _need(
        canonical(bridge) == bridge_raw
        and canonical(tail) == tail_raw
        and sha(bridge_raw) == expected_source
        and bridge["schema"] == "ctcc.native_live.owned_source_data.v1"
        and bridge["environment"] == "live"
        and bridge["origin"] == decode(view["receipt_json"], maximum=65536)["origin"]
        and bridge["account_identity_sha256"]
        == sha(canonical(["live", view["uid"], view["main_uid"]]))
    )
    _need(
        bridge["financial_tail_sha256"] == sha(tail_raw)
        and tail["environment"] == "live"
        and bridge["execution_authority"] is False
        and bridge["account_complete"] is False
        and tail["execution_authority"] is False
        and tail["account_complete"] is False
    )
    if "financial_rows_receipt_json" in document:
        raw = document["financial_rows_receipt_json"].encode("ascii")
        rows = decode(raw, maximum=2097152)
        _need(
            canonical(rows) == raw
            and sha(raw) == document["financial_rows_sha256"]
            and rows["source_revision_sha256"] == expected_source
            and rows["raw_bank_sha256"] == bridge["raw_bank_sha256"]
            and rows["financial_tail_sha256"] == sha(tail_raw)
        )


def _held_scope_v1(source_owner, key):
    from app.database.repositories import native_live_financial_epoch_v1 as repository

    _need(key is repository._SEAL_KEY)
    return repository._require_financial_seal_scope_v1(source_owner, key=_SOURCE_KEY)


def _verify_scope_data_v1(scope, material, view):
    epoch = scope["epoch"]
    _need(type(epoch) is bytes)
    document = _verify_data_v1(material, "epoch", epoch)
    _checked_original_source_documents_v1(document, view)
    _need(
        document["environment"] == "live"
        and document["account_uid"] == view["uid"]
        and document["main_uid"] == view["main_uid"]
        and document["policy_sha256"] == spec.POLICY_SHA256
    )
    previous = None
    for index, raw in enumerate(scope["prefix"], 1):
        sample = _verify_data_v1(material, "sample", raw)
        _checked_original_source_documents_v1(sample, view)
        _need(
            sample["epoch_sha256"] == sha(epoch)
            and sample["epoch_id"] == document["epoch_id"]
            and type(sample["sequence"]) is int
            and sample["sequence"] == index
            and sample["previous_sha256"] == previous
        )
        previous = sha(raw)
    # Full raw fill/bill/position semantic replay happens in sample_document;
    # verification here first excludes collector-forged/rehashed JSON.


def issue_original_genesis_v1(source_owner, *, key):
    return _native_operation_v1(source_owner, key=key, operation="genesis")


def issue_original_sample_v1(source_owner, *, key):
    return _native_operation_v1(source_owner, key=key, operation="sample")


def verify_original_prefix_v1(source_owner, *, key):
    return _native_operation_v1(source_owner, key=key, operation="verify")
