"""V6 adds exact original Stage1 transcript; old V5 bundle stays unchanged."""

import base64
from datetime import datetime
from typing import Literal

from pydantic import model_validator

from app.domain.source_primitives import decode
from app.mie.validation.artifact import _freeze, _verify
from app.mie.validation.contracts import Sha256
from app.mie.validation.native_availability_bundle_v5 import (
    MAX_BUNDLE_BYTES,
    NativeAvailabilityBundleV5,
    WitnessRawRowV5,
    snapshot_public_journal_v5,
)
from app.mie.validation.native_stage1_forensics_v6 import (
    MAX_PACK,
    replay_forensic_pack_v6,
)

WitnessRawRowV6 = WitnessRawRowV5
snapshot_public_journal_v6 = snapshot_public_journal_v5


class NativeAvailabilityBundleV6(NativeAvailabilityBundleV5):
    schema_version: Literal["ctcc.mie.native_availability_raw_bundle.v6"] = (
        "ctcc.mie.native_availability_raw_bundle.v6"
    )
    stage1_forensic_base64: str
    stage1_forensic_sha256: Sha256

    @model_validator(mode="after")
    def retained_stage1(self):
        raw = base64.b64decode(self.stage1_forensic_base64, validate=True)
        if base64.b64encode(raw).decode("ascii") != self.stage1_forensic_base64:
            raise ValueError("availability_stage1_encoding")
        snapshot = replay_forensic_pack_v6(
            raw,
            expected_sha256=self.stage1_forensic_sha256,
            expected_claim_sha256=self.ordinal_claim_sha256,
        )
        if snapshot.status != "completed":
            raise ValueError("availability_stage1_not_completed")
        pack = decode(raw, MAX_PACK)
        files = {
            item["name"]: base64.b64decode(item["raw_base64"], validate=True)
            for item in pack["files"]
        }
        header, outcome = (
            decode(files["attempt.json"], MAX_PACK),
            decode(files["outcome.json"], MAX_PACK),
        )
        if (
            outcome["clock_v4_sha256"] != self.clock_v4.canonical_sha256()
            or header["source_v3_sha256"] != self.clock_v4.source_v3_sha256
            or header["journal_genesis_sha256"]
            != self.clock_v4.source_v3.v2_capture.journal_genesis_sha256
            or header["journal_id"] != self.witness_rows[0].journal_id
        ):
            raise ValueError("availability_stage1_clock_binding")
        if (
            files["health-before.raw"] != self.clock_v4.health_before.raw_bytes()
            or files["health-after.raw"] != self.clock_v4.health_after.raw_bytes()
        ):
            raise ValueError("availability_stage1_health_binding")
        sample = decode(files["sample.json"], MAX_PACK)
        if (
            sample["sample_before"] != self.clock_v4.sample_before.model_dump()
            or sample["sample_after"] != self.clock_v4.sample_after.model_dump()
        ):
            raise ValueError("availability_stage1_bracket_binding")
        expected = self.clock_v4.database_sample.identity.model_dump()
        expected["sampled_at"] = self.clock_v4.database_sample.sampled_at
        cells = sample["observed_rows"][0]
        actual = {}
        for cell in cells:
            value = cell["decoded"]
            if value["type"] == "datetime":
                decoded = datetime.fromisoformat(value["value"]).replace(
                    fold=value["fold"]
                )
            elif value["type"] == "null":
                decoded = None
            else:
                decoded = value["value"]
            if cell["column"] in actual:
                raise ValueError("availability_stage1_duplicate_column")
            actual[cell["column"]] = decoded
        if set(actual) != set(expected) or any(
            type(actual[k]) is not type(expected[k]) or actual[k] != expected[k]
            for k in expected
        ):
            raise ValueError("availability_stage1_decoded_binding")
        return self


def freeze_native_availability_bundle_v6(contract):
    frozen = _freeze(contract, contract_type=NativeAvailabilityBundleV6)
    if len(frozen.payload) > MAX_BUNDLE_BYTES:
        raise ValueError("availability_bundle_budget")
    return frozen


def replay_native_availability_bundle_v6(payload, *, expected_sha256):
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_BUNDLE_BYTES:
        raise ValueError("availability_bundle_budget")
    return _verify(
        payload,
        expected_sha256=expected_sha256,
        contract_type=NativeAvailabilityBundleV6,
    )
