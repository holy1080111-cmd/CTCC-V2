"""Synthetic receipt parser only; no SQL or G12 publication acceptance."""

import copy
from datetime import UTC, datetime, timedelta

import pytest

from scripts import current0058_hermetic_contract_v1 as contract
from scripts import current0058_profile_measurement_v1 as subject


def declared_synthetic_receipt(source):
    start = datetime(2026, 11, 20, tzinfo=UTC)
    names = (
        "claim_write",
        "claim_read",
        "claim_read",
        "publication_write",
        "claim_read",
        "publication_read",
    )
    stages = [
        {
            "stage": name,
            "database_head": "0058",
            "writer_oid": 42,
            "backend_pid": 100 + index,
            "transaction_id": str(1000 + index),
            "started_at": (start + timedelta(seconds=index)).isoformat(),
            "completed_at": (
                start + timedelta(seconds=index, milliseconds=1)
            ).isoformat(),
        }
        for index, name in enumerate(names)
    ]
    return {
        "schema": "ctcc.current0058.g12_custody_component.v1",
        "source": source,
        "case": contract.CUSTODY_CASE[1],
        "database_head": "0058",
        "custody": {
            "database_head": "0058",
            "writer_oid": 42,
            "catalog_sha256": "1" * 64,
            "transcript_sha256": "2" * 64,
            "claim_sha256": "3" * 64,
            "publication_sha256": "4" * 64,
            "stages": stages,
            "claim_count": 1,
            "publication_count": 1,
            "synthetic_private_owner_handoff": True,
            "qualification_performed": False,
            "execution_authority": False,
        },
        "execution_authority": False,
        "trading_acceptance": False,
    }


def test_synthetic_codec_retains_all_six_distinct58_session_receipts_without_authority():
    source = {"DECLARED_SYNTHETIC_SOURCE_NOT_AUTHENTICATED": True}
    value = declared_synthetic_receipt(source)
    assert (
        subject.verify_custody_measurement(contract.canonical(value), source=source)
        == value
    )
    assert (
        len(value["custody"]["stages"]) == 6 and value["execution_authority"] is False
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "head",
        "stage_head",
        "duplicate_xid",
        "boolean_oid",
        "missing_stage",
        "wrong_stage",
        "reversed_clock",
        "naive_clock",
        "source",
        "authority",
        "qualification",
        "owner_handoff",
        "counts",
        "extra_key",
    ],
)
def test_custody_codec_denies_missing_relabelled_reused_or_changed_receipts(mutation):
    source = {"DECLARED_SYNTHETIC_SOURCE_NOT_AUTHENTICATED": True}
    value = copy.deepcopy(declared_synthetic_receipt(source))
    inner = value["custody"]
    if mutation == "head":
        value["database_head"] = "0057"
    elif mutation == "stage_head":
        inner["stages"][0]["database_head"] = "0057"
    elif mutation == "duplicate_xid":
        inner["stages"][1]["transaction_id"] = inner["stages"][0]["transaction_id"]
    elif mutation == "boolean_oid":
        inner["writer_oid"] = True
    elif mutation == "missing_stage":
        inner["stages"].pop()
    elif mutation == "wrong_stage":
        inner["stages"][3]["stage"] = "claim_read"
    elif mutation == "reversed_clock":
        inner["stages"][1]["started_at"] = "2026-11-19T00:00:00+00:00"
    elif mutation == "naive_clock":
        inner["stages"][0]["completed_at"] = "2026-11-20T00:00:00.001000"
    elif mutation == "source":
        value["source"] = {}
    elif mutation == "authority":
        value["execution_authority"] = True
    elif mutation == "qualification":
        inner["qualification_performed"] = True
    elif mutation == "owner_handoff":
        inner["synthetic_private_owner_handoff"] = False
    elif mutation == "counts":
        inner["claim_count"] = 0
    else:
        inner["passed"] = True
    with pytest.raises(ValueError):
        subject.verify_custody_measurement(contract.canonical(value), source=source)
