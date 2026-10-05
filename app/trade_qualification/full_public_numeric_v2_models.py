"""Closed raw V2 math diagnostics; never qualification or execution permits."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, repr=False)
class FullPublicLocationDiagnosticV2:
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        from app.trade_qualification.full_public_numeric_v2_guard import record_document

        return record_document(self, FullPublicLocationDiagnosticV2)[1]

    @property
    def execution_authority(self):
        return False


@dataclass(frozen=True, slots=True, repr=False)
class FullPublicEconomicsDiagnosticV2:
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        from app.trade_qualification.full_public_numeric_v2_guard import record_document

        return record_document(self, FullPublicEconomicsDiagnosticV2)[1]

    @property
    def execution_authority(self):
        return False
