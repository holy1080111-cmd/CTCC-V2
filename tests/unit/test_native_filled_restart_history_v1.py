"""Synthetic immutable-row/RAW replay only; never native/DB/write authority."""

from types import SimpleNamespace

import pytest

from app.database.repositories import native_postsubmit_v1 as repository
from app.demo_automation.native_requests_v2 import NativeDemoRecoveryRequestV1
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import native_close_contracts_v1 as close
from app.trade_qualification import native_postsubmit_reconciliation_v1 as post
from app.trade_qualification import native_reconciliation_facts_v1 as facts
from tests.unit.test_native_close_final_facts_v1 import terminal
from tests.unit.test_native_ledger_contracts_v2 import (
    synthetic as synthetic,  # noqa: PLC0414 -- Pytest collects this imported fixture.
)
from tests.unit.test_native_reconciliation_facts_v1 import (
    target as target,  # noqa: PLC0414 -- Pytest collects this imported fixture.
)


def history_rows(target):
    original, preflight, _, _ = terminal(target)
    assessed = facts.assess_native_reconciliation_v1(target, original)
    planned = close.assess_native_close_preflight_v1(target, original, preflight)
    rows = []

    def append(document, kind="reconciliation"):
        raw = canonical(document)
        index = len(rows) + 1
        # Synthetic identifiers deliberately do NOT purport to satisfy the
        # production SQL outcome hash/journal/actual-XID chain.
        identity = sha(canonical({"synthetic_sequence": index, "capture": document}))
        row = repository.Outcome(
            outcome_id=identity,
            reservation_id=target.reservation_id,
            intent_transition_id=1,
            sequence=index,
            previous_sha256=rows[-1].outcome_id if rows else None,
            observation_kind=kind,
            status="uncertain",
            intent_sha256=target.intent_sha256,
            exchange_request_sha256="c" * 64,
            capture_json=raw.decode(),
            capture_sha256=sha(raw),
            outcome_json="{}",
            ledger_revision=index,
            observed_at=original[0][1].started_at,
            recorded_at=original[0][1].started_at,
        )
        rows.append(row)
        return identity

    def manifest(pages, additions):
        capture_id = sha(canonical({"synthetic_manifest": len(rows)}))
        entries = []
        for index, (role, page) in enumerate(pages):
            metadata = {
                "version": "ctcc.native_read_page_metadata.v1",
                "capture_id": capture_id,
                "page_index": index,
                "role": role,
                "status": page.http_status,
                "path": page.path,
                "parameters": list(page.parameters),
                "request_url": page.request_url,
                "request_signature_sha256": page.request_signature_sha256,
                "read_session_id": page.read_session_id,
                "started_at": page.started_at.isoformat(),
                "started_monotonic_ns": page.started_monotonic_ns,
                "headers_at": page.headers_at.isoformat(),
                "headers_monotonic_ns": page.headers_monotonic_ns,
                "eof_at": page.eof_at.isoformat(),
                "eof_monotonic_ns": page.eof_monotonic_ns,
                "raw_headers_hex": [
                    [key.hex(), value.hex()] for key, value in page.raw_headers
                ],
                "body_bytes": len(page.raw_body),
                "body_sha256": sha(page.raw_body),
                "certificate_sha256": page.certificate_sha256,
                "tls_hostname": page.tls_hostname,
            }
            metadata_id = append(metadata)
            chunks = []
            for offset in range(0, len(page.raw_body), repository._CHUNK):
                raw = page.raw_body[offset : offset + repository._CHUNK]
                chunks.append(
                    append(
                        {
                            "version": "ctcc.native_read_raw_part.v1",
                            "capture_id": capture_id,
                            "page_index": index,
                            "metadata_outcome_id": metadata_id,
                            "offset": offset,
                            "raw_hex": raw.hex(),
                            "raw_sha256": sha(raw),
                        }
                    )
                )
            entries.append(
                {
                    "role": role,
                    "metadata": metadata_id,
                    "parts": chunks,
                    "body_sha256": sha(page.raw_body),
                }
            )
        return append(
            {
                "version": "ctcc.native_read_manifest.v1",
                "capture_id": capture_id,
                "computation": "completed",
                "pages": entries,
                **additions,
            }
        )

    append({"version": "ctcc.native_initial_transport_data.v1"}, "initial")
    original_manifest = manifest(
        original, {"assessment_sha256": sha(assessed.receipt_json)}
    )
    original_id = append(
        {
            "version": "ctcc.native_verified_reconciliation_data.v1",
            "manifest_outcome_id": original_manifest,
            "assessment": decode(assessed.receipt_json),
        }
    )
    preflight_manifest = manifest(
        preflight,
        {
            "purpose": "native_controlled_close_preflight.v1",
            "assessment_sha256": sha(planned.receipt_json),
            "original_filled_outcome_id": original_id,
            "original_filled_receipt_sha256": sha(assessed.receipt_json),
            "preflight_json": planned.receipt_json.decode(),
            "exact_close_order_json": planned.exact_order_json.decode(),
            "exact_close_order_sha256": planned.exact_order_sha256,
        },
    )
    measured, order = decode(planned.receipt_json), decode(planned.exact_order_json)
    append(
        {
            "version": "ctcc.native_controlled_close_intent.v1",
            "reservation_id": target.reservation_id,
            "original_event_key": target.request.origin.original_event_key,
            "original_entry_request_sha256": sha(target.request_json.encode()),
            "original_entry_intent_sha256": target.intent_sha256,
            "original_credential_session_sha256": target.request.credential_session_sha256,
            "original_filled_outcome_id": original_id,
            "preflight_manifest_outcome_id": preflight_manifest,
            "preflight_sha256": sha(planned.receipt_json),
            "exact_close_order_json": planned.exact_order_json.decode(),
            "close_request_sha256": planned.exact_order_sha256,
            "close_client_order_id": order["clOrdId"],
            "original_entry_order_id": measured["entry_order_id"],
            **{
                name: measured[name]
                for name in (
                    "position_id",
                    "protection_algo_id",
                    "protection_client_order_id",
                    "position_mode",
                    "closing_semantics",
                )
            },
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    data_target = repository.NativeTargetReadbackV1(
        target.request.scope,
        object(),
        target.reservation_id,
        target.request,
        target.request_json,
        SimpleNamespace(sha256=target.intent_sha256),
        None,
        None,
        b"{}",
        b"{}",
        b"{}",
        "a" * 64,
        "b" * 64,
        (),
        "uncertain",
        3,
        target,
    )
    return data_target, tuple(rows), original, preflight


def change_row(rows, predicate, mutate):
    output = []
    for row in rows:
        document = decode(row.capture_json.encode(), 140000)
        if predicate(document):
            values = repository._row(row)
            mutate(document)
            values["capture_json"] = canonical(document).decode()
            row = repository.Outcome(**values)
        output.append(row)
    return tuple(output)


def test_actual_raw_manifests_reconstruct_only_historical_data(target):
    data_target, rows, original, preflight = history_rows(target)
    before = tuple(repository._RECOVERY_CLOSE_HISTORIES_V1)
    value = repository._recovery_close_history_from_rows_v1(data_target, rows, ())
    assert (
        value.original_observations == original
        and value.preflight_observations == preflight
    )
    assert value.close_outcome_id == rows[-1].outcome_id
    assert value.original_assessment.filled_and_protected is True
    assert not value.execution_authority and not value.order_retry_authority
    assert tuple(repository._RECOVERY_CLOSE_HISTORIES_V1) == before


@pytest.mark.parametrize(
    "field",
    (
        "original_event_key",
        "original_entry_request_sha256",
        "original_entry_intent_sha256",
        "original_credential_session_sha256",
        "close_request_sha256",
        "close_client_order_id",
        "position_id",
        "protection_algo_id",
        "protection_client_order_id",
    ),
)
def test_cross_event_or_changed_fixed_close_rejects(target, field):
    data_target, rows, _, _ = history_rows(target)
    altered = change_row(
        rows,
        lambda document: (
            document["version"] == "ctcc.native_controlled_close_intent.v1"
        ),
        lambda document: document.update({field: "foreign"}),
    )
    with pytest.raises(repository.NativePostsubmitRepositoryError):
        repository._recovery_close_history_from_rows_v1(data_target, altered, ())


def test_duplicate_unique_close_is_not_a_retry_plan(target):
    data_target, rows, _, _ = history_rows(target)
    with pytest.raises(repository.NativePostsubmitRepositoryError):
        repository._recovery_close_history_from_rows_v1(
            data_target, (*rows, rows[-1]), ()
        )


def test_missing_old_raw_part_is_not_replaced_by_a_receipt(target):
    data_target, rows, _, _ = history_rows(target)
    removed = next(
        row
        for row in rows
        if decode(row.capture_json.encode())["version"]
        == "ctcc.native_read_raw_part.v1"
    )
    with pytest.raises((repository.NativePostsubmitRepositoryError, KeyError)):
        repository._recovery_close_history_from_rows_v1(
            data_target, tuple(row for row in rows if row is not removed), ()
        )


def test_stored_complete_pages_remain_terminal_scan_roots(target):
    _, rows, original, preflight = history_rows(target)
    received = repository._recovery_received_roots_from_rows_v1(rows)
    assert all(page.raw_body in received for _, page in original + preflight)


def test_stored_post_hex_is_decoded_without_inventing_eof(target):
    _, rows, _, _ = history_rows(target)
    body = b'{"code":"0","data":[]}'
    response = repository.Outcome(
        outcome_id="f" * 64,
        capture_json=canonical(
            {
                "version": "ctcc.native_initial_transport_data.v1",
                "raw_body_hex": body.hex(),
                "raw_body_sha256": sha(body),
                "received_eof": True,
                "received_overflow": False,
            }
        ).decode(),
    )
    assert body in repository._recovery_received_roots_from_rows_v1((*rows, response))
    damaged = repository.Outcome(
        outcome_id="f" * 64,
        capture_json=response.capture_json.replace(
            '"received_eof":true', '"received_eof":false'
        ),
    )
    with pytest.raises(repository.NativePostsubmitRepositoryError):
        repository._recovery_received_roots_from_rows_v1((*rows, damaged))


@pytest.mark.parametrize(
    "raw", ({"reservation_id": "x" * 64}, {"reservation_id": "a" * 64, "passed": True})
)
def test_constructed_recovery_request_cannot_add_authority_or_bypass_exact_id(raw):
    request = NativeDemoRecoveryRequestV1.model_construct(**raw)
    object.__getattribute__(request, "__dict__").update(raw)
    with pytest.raises(post.NativePostsubmitError):
        post._native_recovery_request_id_v1(request)


def test_missing_source_registry_never_means_empty_history():
    with pytest.raises(post.NativePostsubmitError):
        post._retained_native_recovery_history_v1(object(), key=object())
