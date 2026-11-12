"""Synthetic ordering controls for the inert multi-child holdout reader."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.source_primitives import sha
from app.mie.validation import controlled_fitted_campaign_reader_v1 as reader
from app.mie.validation import controlled_fitted_holdout_reader_v1 as single_reader
from app.mie.validation import gate3_fitted_candidate_custody_v1 as custody_module
from app.mie.validation.gate3_fitted_candidate_custody_v1 import (
    FittedCandidateInputsV1,
    FittedEvaluationReservationV1,
    Gate3FittedCandidateCustodyV1,
    Gate3FittedCustodyError,
)
from app.mie.validation.prospective_campaign_v2 import (
    CampaignChildV2,
    ProspectiveCampaignV2,
)


def _pin(number: int) -> str:
    return f"{number:064x}"


class _Binding:
    def __init__(self, seal_sha256: str, coordinate_sha256: str):
        self.preregistration_sha256 = seal_sha256
        self.holdout_coordinate_sha256 = coordinate_sha256

    def model_dump(self, *, mode: str):
        assert mode == "python"
        return {
            "preregistration_sha256": self.preregistration_sha256,
            "holdout_coordinate_sha256": self.holdout_coordinate_sha256,
            "fitted_model_sha256": _pin(50),
        }


def _case(monkeypatch, tmp_path: Path):
    minimum = datetime(2026, 11, 12, 8, tzinfo=UTC)
    receipts = []
    requests = []
    children = []
    refs = []
    for index in range(2):
        seal = object()
        seal_pin = _pin(index + 1)
        coordinate_pin = _pin(index + 10)
        receipt_pin = _pin(index + 20)
        binding = _Binding(seal_pin, coordinate_pin)
        inputs = FittedCandidateInputsV1(
            binding=SimpleNamespace(contract=binding),
            expected_binding_sha256=_pin(50),
            batch=None,
            study=None,
            trial_result=None,
            legacy_scores=None,
            preregistration=seal,
            expected_preregistration_sha256=seal_pin,
            source_archive_sha256=_pin(60),
        )
        scoped_custody = object.__new__(Gate3FittedCandidateCustodyV1)
        scoped_custody._role = "evaluator"
        scoped_custody._expected = (
            "127.0.0.1",
            5432,
            "ctcc_gate3",
            "ctcc_evaluator",
            42,
            45,
            43,
            44,
            45,
        )
        requests.append(
            reader.FittedCampaignChildReadV1(
                custody=scoped_custody,
                inputs=inputs,
                expected_ack_sha256=_pin(70 + index),
                receipt_bytes=b"synthetic receipt",
                expected_receipt_sha256=receipt_pin,
                manifest_path=tmp_path / f"manifest-{index}",
                dataset_path=tmp_path / f"dataset-{index}",
            )
        )
        children.append((seal, object()))
        refs.append(
            CampaignChildV2.model_construct(
                seal_sha256=seal_pin, coordinate_plan_sha256=coordinate_pin
            )
        )
        receipts.append(
            SimpleNamespace(
                preregistration_sha256=seal_pin,
                recorded_at=minimum + timedelta(seconds=index),
                holdout_dataset=SimpleNamespace(
                    content_sha256=sha(f"dataset-{index}".encode()),
                    manifest_sha256=sha(f"manifest-{index}".encode()),
                ),
            )
        )
    campaign = ProspectiveCampaignV2.model_construct(
        children=tuple(refs), first_permitted_access_at=minimum
    )
    monkeypatch.setattr(
        reader, "verify_prospective_campaign_v2", lambda *_a, **_k: campaign
    )
    monkeypatch.setattr(reader, "_verified", lambda inputs: inputs.binding.contract)

    def verified_receipt(inputs, _bytes, _pin):
        return receipts[int(inputs.expected_preregistration_sha256, 16) - 1]

    monkeypatch.setattr(reader, "_verified_receipt", verified_receipt)
    monkeypatch.setattr(single_reader, "_verified_receipt", verified_receipt)

    async def acknowledged(_self, inputs, *, expected_ack_sha256):
        assert expected_ack_sha256 in (_pin(70), _pin(71))
        return SimpleNamespace(
            seal_sha256=inputs.expected_preregistration_sha256,
            database_oid=42,
            binding=inputs.binding.contract,
        )

    monkeypatch.setattr(
        Gate3FittedCandidateCustodyV1, "read_acknowledged", acknowledged
    )
    return (
        {
            "campaign_bytes": b"synthetic campaign",
            "expected_campaign_sha256": _pin(90),
            "campaign_id": "synthetic",
            "children": tuple(children),
            "feature_plan": object(),
            "expected_feature_plan_sha256": _pin(91),
            "minimum_candidate_bars": 1,
            "requests": tuple(requests),
        },
        receipts,
        minimum,
    )


@pytest.mark.asyncio
async def test_second_child_reservation_refusal_precedes_every_path_access(
    monkeypatch, tmp_path
):
    arguments, receipts, minimum = _case(monkeypatch, tmp_path)
    seen = []

    async def reserve(
        _self,
        inputs,
        *,
        expected_ack_sha256,
        receipt_sha256,
        dataset_sha256,
        not_before,
    ):
        seen.append(inputs.expected_preregistration_sha256)
        assert expected_ack_sha256 in (_pin(70), _pin(71))
        assert not_before == receipts[-1].recorded_at > minimum
        if len(seen) == 2:
            raise Gate3FittedCustodyError("synthetic_second_reservation_refused")
        return FittedEvaluationReservationV1(
            seal_sha256=inputs.expected_preregistration_sha256,
            binding_sha256=inputs.expected_binding_sha256,
            acknowledgement_xid="11",
            receipt_sha256=receipt_sha256,
            dataset_sha256=dataset_sha256,
            attempt_sha256=_pin(80),
            database_oid=42,
            evaluator_oid=45,
            reservation_xid="12",
            reserved_at=not_before,
            database_read_at=not_before + timedelta(seconds=1),
        )

    def no_path_access(*_args, **_kwargs):
        pytest.fail("holdout path accessed before every reservation committed")

    monkeypatch.setattr(Gate3FittedCandidateCustodyV1, "reserve_evaluation", reserve)
    monkeypatch.setattr(reader, "_read_pinned_reserved_pair", no_path_access)
    monkeypatch.setattr(reader.os.path, "samefile", no_path_access)
    with pytest.raises(Gate3FittedCustodyError, match="second_reservation_refused"):
        await reader.read_fitted_campaign_once(**arguments)
    assert seen == [_pin(1), _pin(2)]
    assert not any(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_global_time_refusal_precedes_every_path_access(monkeypatch, tmp_path):
    arguments, receipts, minimum = _case(monkeypatch, tmp_path)

    async def reserve(_self, _inputs, **kwargs):
        assert kwargs["not_before"] == receipts[-1].recorded_at > minimum
        raise Gate3FittedCustodyError("FITTED_CUSTODY_GLOBAL_TIME_NOT_REACHED")

    def no_path_access(*_args, **_kwargs):
        pytest.fail("holdout path accessed before database time gate")

    monkeypatch.setattr(Gate3FittedCandidateCustodyV1, "reserve_evaluation", reserve)
    monkeypatch.setattr(reader, "_read_pinned_reserved_pair", no_path_access)
    monkeypatch.setattr(reader.os.path, "samefile", no_path_access)
    with pytest.raises(Gate3FittedCustodyError, match="GLOBAL_TIME_NOT_REACHED"):
        await reader.read_fitted_campaign_once(**arguments)
    assert not any(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_complete_campaign_reuses_pinned_reads_only_after_both_reservations(
    monkeypatch, tmp_path
):
    arguments, receipts, _ = _case(monkeypatch, tmp_path)
    for request in arguments["requests"]:
        request.manifest_path.write_bytes(request.manifest_path.name.encode())
        request.dataset_path.write_bytes(request.dataset_path.name.encode())
    order = []

    async def reserve(_self, inputs, **kwargs):
        index = int(inputs.expected_preregistration_sha256, 16) - 1
        order.append(("reserve", index))
        at = kwargs["not_before"]
        assert at == receipts[-1].recorded_at
        return FittedEvaluationReservationV1(
            seal_sha256=inputs.expected_preregistration_sha256,
            binding_sha256=inputs.expected_binding_sha256,
            acknowledgement_xid="11",
            receipt_sha256=kwargs["receipt_sha256"],
            dataset_sha256=kwargs["dataset_sha256"],
            attempt_sha256=_pin(80 + index),
            database_oid=42,
            evaluator_oid=45,
            reservation_xid=str(12 + index),
            reserved_at=at,
            database_read_at=at + timedelta(seconds=1),
        )

    def read_pair(**kwargs):
        index = int(kwargs["inputs"].expected_preregistration_sha256, 16) - 1
        assert order[:2] == [("reserve", 0), ("reserve", 1)]
        order.append(("read", index))
        return single_reader._read_pinned_reserved_pair(**kwargs)

    monkeypatch.setattr(Gate3FittedCandidateCustodyV1, "reserve_evaluation", reserve)
    monkeypatch.setattr(reader, "_read_pinned_reserved_pair", read_pair)
    result = await reader.read_fitted_campaign_once(**arguments)
    assert order == [("reserve", 0), ("reserve", 1), ("read", 0), ("read", 1)]
    assert tuple(item.dataset_bytes for item in result.child_reads) == (
        b"dataset-0",
        b"dataset-1",
    )
    assert len(set(result.reservation_sha256s)) == 2
    assert not result.independent_campaign_custody_verified
    assert not result.execution_authority


@pytest.mark.asyncio
async def test_sql_time_gate_zero_row_rolls_back_without_success(monkeypatch):
    events = []

    class _Result:
        def __init__(self, *, facts=None):
            self._facts = facts

        def mappings(self):
            return self

        def one(self):
            return self._facts

        def one_or_none(self):
            return None

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, _exc, _tb):
            events.append("rollback" if exc_type else "commit")

        def begin(self):
            return self

        async def execute(self, statement, _parameters=None):
            if statement is custody_module._SESSION:
                return _Result(
                    facts={
                        "direct_login": True,
                        "durable_primary": True,
                        "tls": True,
                        "database_name": "ctcc_gate3",
                        "login_name": "ctcc_evaluator",
                        "database_oid": 42,
                        "login_oid": 45,
                        "xid": "11",
                    }
                )
            if statement is custody_module._APPEND_EVALUATION_NOT_BEFORE:
                assert "pg_catalog.clock_timestamp()" in statement.text
                return _Result()
            return _Result()

    scoped_custody = object.__new__(Gate3FittedCandidateCustodyV1)
    scoped_custody._expected = (
        "127.0.0.1",
        5432,
        "ctcc_gate3",
        "ctcc_evaluator",
        42,
        45,
        43,
        44,
        45,
    )
    scoped_custody._factory = _Session
    monkeypatch.setattr(
        Gate3FittedCandidateCustodyV1, "_require_engine", lambda _self: None
    )
    with pytest.raises(Gate3FittedCustodyError, match="GLOBAL_TIME_NOT_REACHED"):
        await scoped_custody._transaction(
            custody_module._APPEND_EVALUATION_NOT_BEFORE,
            {"minimum": datetime(2026, 11, 12, tzinfo=UTC)},
            read=False,
            require_write_row=True,
        )
    assert "rollback" in events and "commit" not in events
