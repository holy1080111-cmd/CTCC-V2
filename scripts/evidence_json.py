"""Decode evidence without discarding duplicate fields or non-JSON constants.

Callers retain their own byte budgets, native-file checks and typed schemas.
This parser makes no source-authenticity or execution-authority assertion.
"""

from __future__ import annotations

import json


def read_evidence_json(raw: str | bytes):
    if type(raw) not in (str, bytes):
        raise ValueError("evidence_json_native_text_or_bytes_required")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("evidence_json_duplicate_key")
            result[key] = value
        return result

    def reject_constant(_):
        raise ValueError("evidence_json_non_json_constant")

    return json.loads(
        raw, object_pairs_hook=unique_object, parse_constant=reject_constant
    )
