"""Actual runtime SQL must equal separately frozen literal review bytes."""

import hashlib
import re

import pytest

from app.domain import native_closed_report_schema_v55 as report
from app.domain import native_filled_flat_closure_schema_v55 as filled

REVIEW = {
    "qualification_native_canonical_v55": {
        "sha256": "77a1b9dce861b7f7bc888ed6f2ea4c6b873fa92519ce6da8f050b58d02f81d2d",
        "bytes": 984,
    },
    "qualification_native_clock_v55": {
        "sha256": "af21762a1d4aaced0b7a3dffd7997b99a5e728a005aa084e99886cc3333053e2",
        "bytes": 369,
    },
    "qualification_native_filled_flat_data_v55": {
        "sha256": "d7c006496a461502de13eaae36df528169fda43c8d84c23d8705dcb6e7889f6a",
        "bytes": 53132,
    },
    "qualification_native_manifest_v55": {
        "sha256": "af2a2dd5d7a24cb64075cb4cf8dd4aadcaab0973fa3739639c02607226f70fef",
        "bytes": 10059,
    },
    "qualification_native_number_v55": {
        "sha256": "35978322e3f79b661ac10163d880efaf77aa1b945057ee932240862e6ddd32a2",
        "bytes": 385,
    },
    "qualification_native_plain_v55": {
        "sha256": "8972054e79f3876c2f8355b749f36145a9b3585b1002ab82723130dc02fd949a",
        "bytes": 1212,
    },
    "qualification_native_projection_eligible_v55": {
        "sha256": "938531a3886dac4647a5d6a4f9fcd8459f1839167d658c1cf8a53c65b3e9c849",
        "bytes": 660,
    },
    "qualification_native_report_eligible_v55": {
        "sha256": "32473711d1aef5426fd5cf4761a1f204b181d4ecc3f47e4bd355b7a07be3a116",
        "bytes": 48505,
    },
    "qualification_native_rows_v55": {
        "sha256": "5759184df87076fa467a4763fe3a2828e306fd3aa32b565dbfb9ef48acaa26e6",
        "bytes": 2122,
    },
    "qualification_projection_binding": {
        "sha256": "d6ae191a020d0c037e2c71849ede1dcf2c6d29546d724b1de76c286d78cb1cf5",
        "bytes": 762,
    },
    "qualification_reservation_update": {
        "sha256": "cce3e2979b4cd9496e8740f8dfba24fc161e874afb1970b308ee0f12f43b50c4",
        "bytes": 41110,
    },
    "qualification_spool_binding": {
        "sha256": "01cdaa745234ace66e7c22deaa6c484afbe6dbcf371c32cba2eb0f078db99f3a",
        "bytes": 2395,
    },
    "REPORT_POLICY": {
        "sha256": "d3f1cbcb182973a73764af31b179b7ade152945db5edbbe53b12c099a8c09165",
        "bytes": 474,
    },
}


def _actual_exports():
    result = {}
    for statement in (*filled.statements(), *report.statements()):
        match = re.search(
            r"CREATE(?: OR REPLACE)? FUNCTION public\.([a-z0-9_]+)\(", statement
        )
        if match is not None:
            name = match.group(1)
            assert name not in result
            result[name] = statement.encode("utf-8")
    result["REPORT_POLICY"] = report.REPORT_POLICY_JSON.encode("ascii")
    assert set(result) == set(REVIEW)
    return result


@pytest.mark.parametrize("name", sorted(REVIEW))
def test_actual_generated_SQL_equals_independently_frozen_review(name):
    raw = _actual_exports()[name]
    assert len(raw) == REVIEW[name]["bytes"]
    assert hashlib.sha256(raw).hexdigest() == REVIEW[name]["sha256"]
