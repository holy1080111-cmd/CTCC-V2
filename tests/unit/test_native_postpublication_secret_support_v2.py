"""Complete-body secret regression, synthetic journal DATA only.

This fixture is sufficient for the bounded scanner. It is deliberately not a
public packet, native source/custody proof, G1 result or qualification owner.
"""

import copy

import pytest

from app.domain.source_primitives import canonical, sha
from app.public_market_source.public_runtime_custody_v4 import RuntimeBundleDirectoryV4
from app.trade_qualification import native_postpublication_support_v2 as support

LATER = "SYNTHETIC_ONLY_LATER_SIGNATURE_4BCF992D"
TOKENS = (
    "SYNTHETIC_ONLY_API_KEY_01",
    "SYNTHETIC_ONLY_SECRET_02",
    "SYNTHETIC_ONLY_PASSPHRASE_03",
    "SYNTHETIC_ONLY_SESSION_04",
    "SYNTHETIC_ONLY_EARLIER_SIGNATURE_05",
    LATER,
)


def journal(*, note="safe", escaped=False, damage=None):
    if escaped:
        spelling = "".join("\\u" + format(ord(char), "04x") for char in note)
        body = ('{"data":[{"note":"' + spelling + '"}]}').encode()
        cut = body.index(b"\\u") + len(spelling) // 2
    else:
        body = canonical({"data": [{"note": note}]})
        marker = note.encode()
        cut = body.index(marker) + max(1, len(marker) // 2)
    chunks = [body[:cut], body[cut:]]
    assert all(LATER.encode() not in part for part in chunks)
    stamp = {"utc_ns": 1, "monotonic_ns": 1}
    parts = [
        (
            "request",
            {
                "id": 0,
                "role": "synthetic",
                "method": "GET",
                "origin": "https://www.okx.com",
                "endpoint": "/api/v5/market/ticker",
                "query": [],
                "started": stamp,
                "request_headers": [],
            },
            None,
        ),
        (
            "headers",
            {
                "id": 0,
                "received": stamp,
                "status": 200,
                "tls": {},
                "safe_headers": [["content-length", str(len(body))]],
                "truncated": False,
            },
            None,
        ),
        *(
            (
                "chunk",
                {
                    "id": 0,
                    "received": stamp,
                    "observed_bytes": len(part),
                    "observed_sha256": sha(part),
                    "truncated": False,
                },
                part,
            )
            for part in chunks
        ),
        (
            "body_complete",
            {
                "id": 0,
                "completed": stamp,
                "body_bytes": len(body),
                "body_sha256": sha(body),
            },
            None,
        ),
        ("response_closed", {"id": 0, "completed": stamp}, None),
        ("packet", {}, canonical({"synthetic_data_only": True})),
    ]
    if damage == "missing_close":
        parts.pop(-2)
    elif damage == "missing_complete":
        parts.pop(-3)
    elif damage == "missing_chunk":
        parts.pop(2)
    elif damage == "duplicate_headers":
        parts.insert(2, copy.deepcopy(parts[1]))
    elif damage == "truncated_chunk":
        parts[2][1]["truncated"] = True
    plan = canonical({"schema_version": "ctcc.public.initial_runtime_plan.v4"})
    previous = sha(plan)
    files, events = {"plan.json": plan}, []
    for index, (kind, metadata, raw) in enumerate(parts):
        event = {
            "schema_version": "ctcc.public.runtime_event.v1",
            "index": index,
            "kind": kind,
            "plan_sha256": sha(plan),
            "previous_sha256": previous,
            "metadata": metadata,
            "raw_sha256": None if raw is None else sha(raw),
            "raw_bytes": None if raw is None else len(raw),
        }
        encoded = canonical(event)
        files[f"event-{index:04d}.json"] = encoded
        if raw is not None:
            files[f"event-{index:04d}.raw"] = raw
        events.append(event)
        previous = sha(encoded)
    files["summary.json"] = canonical(
        {
            "schema_version": "ctcc.public.runtime_summary.v1",
            "plan_sha256": sha(plan),
            "event_count": len(events),
            "head_sha256": previous,
            "disposition": "captured",
            "completed": stamp,
            "packet_sha256": events[-1]["raw_sha256"],
            "execution_authority": False,
            "measured_availability_eligible": False,
            "original_source_verified": False,
            "account_complete": False,
        }
    )
    return RuntimeBundleDirectoryV4(files), tuple(events), body


def test_closed_safe_body_is_scanned_without_modifying_bytes():
    directory, events, body = journal()
    original = dict(directory._files)
    assert support._secret_public_journal(directory, events, TOKENS) is None
    assert directory._files == original
    assert body == b'{"data":[{"note":"safe"}]}'


@pytest.mark.parametrize("escaped", [False, True])
def test_later_signature_across_fragment_boundary_is_rejected(escaped):
    directory, events, body = journal(note=LATER, escaped=escaped)
    original = dict(directory._files)
    assert all(
        LATER.encode() not in raw
        for name, raw in original.items()
        if name.endswith(".raw")
    )
    assert (LATER.encode() not in body) is escaped
    with pytest.raises(ValueError, match="^credential_echo_rejected$"):
        support._secret_public_journal(directory, events, TOKENS)
    assert directory._files == original


@pytest.mark.parametrize(
    "damage",
    [
        "missing_close",
        "missing_complete",
        "missing_chunk",
        "duplicate_headers",
        "truncated_chunk",
    ],
)
def test_self_consistent_hash_chain_cannot_hide_incomplete_http_body(damage):
    directory, events, _ = journal(damage=damage)
    with pytest.raises(ValueError):
        support._secret_public_journal(directory, events, TOKENS)
