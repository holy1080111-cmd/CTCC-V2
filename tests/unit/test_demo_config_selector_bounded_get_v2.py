"""Public pure selector DATA tests only; no client, signature, source or order admission."""

import json

import pytest

from app.exchange.okx.private_rest import (
    DemoConfigSelectorObservationV2,
    _demo_selector_append_chunk_v2,
    _demo_selector_json_constant_v2,
    _demo_selector_json_float_v2,
    _demo_selector_json_pairs_v2,
)


def _parse(raw):
    return json.loads(
        raw,
        object_pairs_hook=_demo_selector_json_pairs_v2,
        parse_constant=_demo_selector_json_constant_v2,
        parse_float=_demo_selector_json_float_v2,
    )


def test_exact_raw_byte_boundary_and_failed_overflow_preserve_prior_chunks():
    chunks = []
    size = _demo_selector_append_chunk_v2(chunks, 0, b"a" * 32768)
    size = _demo_selector_append_chunk_v2(chunks, size, b"b" * 32768)
    assert size == 65536 and b"".join(chunks) == b"a" * 32768 + b"b" * 32768
    with pytest.raises(ValueError, match="response_bound"):
        _demo_selector_append_chunk_v2(chunks, size, b"c")
    assert len(chunks) == 2 and len(b"".join(chunks)) == 65536


def test_empty_chunks_do_not_expand_retained_buffer_list():
    chunks = []
    for _ in range(1000):
        assert _demo_selector_append_chunk_v2(chunks, 0, b"") == 0
    assert chunks == []


def test_foreign_chunk_or_container_cannot_invoke_callbacks():
    class Foreign:
        def __len__(self):
            raise AssertionError("foreign length callback invoked")

        def append(self, _item):
            raise AssertionError("foreign append callback invoked")

    with pytest.raises(ValueError, match="stream_invalid"):
        _demo_selector_append_chunk_v2([], 0, Foreign())
    with pytest.raises(ValueError, match="stream_invalid"):
        _demo_selector_append_chunk_v2(Foreign(), 0, b"x")
    with pytest.raises(ValueError, match="stream_invalid"):
        _demo_selector_append_chunk_v2([], False, b"x")


@pytest.mark.parametrize(
    "raw",
    [
        b'{"code":"0","code":"1","data":[]}',
        b'{"code":"0","data":[{"uid":"123","uid":"456"}]}',
        b'{"code":"0","data":[{"nested":{"x":1,"x":2}}]}',
    ],
)
def test_duplicate_json_is_rejected_at_original_parser(raw):
    with pytest.raises(ValueError, match="duplicate_json"):
        _parse(raw)


@pytest.mark.parametrize(
    "number", ["NaN", "Infinity", "-Infinity", "1e10000", "-1e10000"]
)
def test_nonfinite_json_is_not_accepted(number):
    with pytest.raises(ValueError, match="nonfinite_json"):
        _parse('{"code":"0","data":[{"value":' + number + "}]}")


def test_finite_numbers_and_original_raw_utf8_are_preserved_as_data():
    raw = '{"code":"0","data":[{"uid":"123","mainUid":"123","note":"原始值","n":1.25}]}'.encode()
    parsed = _parse(raw)
    assert parsed["data"][0]["note"] == "原始值"
    assert parsed["data"][0]["n"] == 1.25
    observation = DemoConfigSelectorObservationV2(
        tuple(parsed["data"]), raw, "not-a-real-signature"
    )
    assert observation.raw_body is raw
    assert (
        observation.account_complete is False
        and observation.execution_authority is False
    )
    assert "not-a-real-signature" not in repr(observation) and "原始值" not in repr(
        observation
    )
