"""Pure ledger coverage/replay tests; synthetic account claims are not authority."""

from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Context, Decimal, Inexact, localcontext
from fractions import Fraction
from zoneinfo import ZoneInfo

import pytest
from pydantic import create_model, model_serializer

from app.database.repositories.qualification_ledger import (
    QualificationLedgerRepository,
    _canonical_json_sha256,
)
from app.trade_qualification import reservations as module
from app.trade_qualification.reservations import (
    AccountLedgerClaims,
    LedgerScope,
    LedgerScopeState,
    QualificationLedgerError,
    ReservationReceipt,
    ReservationRequest,
    RiskCoverage,
    ScenarioOperands,
    canonical,
    checked,
    decode,
    digest,
    exact_coverage,
    prepare_reservation,
    reservation_id,
    validate_claims,
)
from tests.unit.qualification_ledger_fixtures import ledger_fixture
from tests.unit.test_qualification_portfolio import reservation

D = Decimal


@pytest.fixture(scope="module", params=("long", "short"))
def fixture(request):
    return ledger_fixture(request.param)


def test_real_two_scenarios_and_exact_coverage(fixture):
    coverage, result = prepare_reservation(
        fixture.request, fixture.claims, (), observed_at=fixture.now
    )
    assert result.passed
    assert coverage.candidate.entry == fixture.request.origin.candidate.candidate_entry
    assert coverage.execution.entry == result.economics.executable_reference
    assert coverage.candidate.stop_loss == coverage.execution.stop_loss
    for sample in (coverage.candidate, coverage.execution):
        quantity = Fraction(sample.contracts) * Fraction(sample.contract_value)
        assert Fraction(coverage.risk_amount) >= quantity * (
            abs(Fraction(sample.entry) - Fraction(sample.stop_loss))
            + Fraction(sample.cost_per_base)
        )
        assert (
            Fraction(coverage.margin_amount)
            >= quantity * Fraction(sample.entry) / sample.leverage
        )
        assert Fraction(coverage.notional_amount) >= quantity * Fraction(sample.entry)
    assert coverage.execution_authority is False
    assert coverage.all_fill_prices_covered is False


@pytest.mark.parametrize("kind", (ReservationRequest, AccountLedgerClaims))
def test_canonical_json_roundtrip_and_noncanonical_reject(fixture, kind):
    value = fixture.request if kind is ReservationRequest else fixture.claims
    raw = canonical(value)
    assert decode(raw, kind) == value
    assert digest(value) == digest(decode(raw, kind))
    with pytest.raises(QualificationLedgerError):
        decode(" " + raw, kind)


def test_hash_of_verified_canonical_text_matches_contract_digest(fixture):
    raw = canonical(fixture.request)
    assert _canonical_json_sha256(raw) == digest(fixture.request)


@pytest.mark.parametrize(
    "value", (" synthetic", "123 ", "", "-1", "1.0", 123, True, "1" * 33)
)
def test_exact_uid_no_normalization(value):
    with pytest.raises(ValueError):
        LedgerScope(account_id=value, settlement_currency="USDT")


@pytest.mark.parametrize("field", tuple(module.LedgerModel.model_fields))
@pytest.mark.parametrize("value", (True, 0, 1, "false", None))
def test_authority_flags_are_exact_false(field, value):
    with pytest.raises(ValueError):
        LedgerScope(account_id="123", settlement_currency="USDT", **{field: value})


def test_fraction_ceiling_independent_of_hostile_context():
    sample = ScenarioOperands(
        entry=D("1.00000000000000000001"),
        stop_loss=D("0.5"),
        cost_per_base=D("0.00000000000000000001"),
        contracts=D("0.00000000000000000001"),
        contract_value=D("0.00000000000000000001"),
        leverage=3,
    )
    expected = exact_coverage(sample, sample)
    with localcontext(Context(prec=2, traps=[Inexact])):
        assert exact_coverage(sample, sample) == expected
    assert all(value == D("0.00000000000000000001") for value in expected.values())


@pytest.mark.parametrize(
    "change", ("extra", "subclass", "nested_subclass", "generator", "nan", "flag")
)
def test_dirty_input_rejected_before_serializer(fixture, change):
    value = fixture.request
    if change == "extra":
        value = value.model_copy(update={"hidden": "secret"})
    elif change == "subclass":
        kind = create_model("OtherRequest", __base__=ReservationRequest)
        value = kind.model_construct(**value.__dict__)
    elif change == "nested_subclass":

        class Trap(LedgerScope):
            @model_serializer
            def trap(self):
                pytest.fail("serializer must never run")

        value = value.model_copy(
            update={"scope": Trap.model_construct(**value.scope.__dict__)}
        )
    elif change == "generator":
        account = value.risk_inputs.account.model_copy(
            update={"positions": (x for x in ())}
        )
        value = value.model_copy(
            update={
                "risk_inputs": value.risk_inputs.model_copy(update={"account": account})
            }
        )
    elif change == "nan":
        value = value.model_copy(
            update={"quote": value.quote.model_copy(update={"bid": D("NaN")})}
        )
    else:
        value = value.model_copy(update={"execution_authority": True})
    with pytest.raises(ValueError):
        checked(value, ReservationRequest)


@pytest.mark.parametrize(
    "change",
    ("armed", "live_trading", "missing_account", "expired", "future", "account"),
)
def test_fresh_guards_and_clocks_recomputed(fixture, change):
    request, claims, now = fixture.request, fixture.claims, fixture.now
    if change in ("armed", "live_trading"):
        authority = claims.authority.model_copy(update={change: change != "armed"})
        claims = claims.model_copy(update={"authority": authority})
        request = request.model_copy(
            update={
                "risk_inputs": request.risk_inputs.model_copy(
                    update={"authority": authority}
                )
            }
        )
    elif change == "missing_account":
        request = request.model_copy(
            update={
                "risk_inputs": request.risk_inputs.model_copy(update={"account": None})
            }
        )
    elif change == "expired":
        now = request.origin.deadline
    elif change == "future":
        now -= timedelta(seconds=5)
    else:
        claims = claims.model_copy(
            update={"scope": LedgerScope(account_id="999", settlement_currency="USDT")}
        )
    with pytest.raises(ValueError):
        prepare_reservation(request, claims, (), observed_at=now)


def test_utc_equivalent_clock_and_claims(fixture):
    other = fixture.now.astimezone(timezone(timedelta(hours=8)))
    assert prepare_reservation(
        fixture.request, fixture.claims, (), observed_at=fixture.now
    ) == prepare_reservation(fixture.request, fixture.claims, (), observed_at=other)
    assert validate_claims(fixture.claims, other) == fixture.claims


def test_event_key_independent_of_report_and_scope_separated(fixture):
    key = fixture.request.origin.original_event_key
    first = reservation_id(fixture.request.scope, key)
    assert len(first) == 64
    other_scope = LedgerScope(account_id="987", settlement_currency="USDT")
    assert reservation_id(other_scope, key) != first
    # ID function deliberately has NO report input, expiry or current clock.
    assert reservation_id(fixture.request.scope, key) == first


def test_receipt_and_coverage_cannot_underreserve(fixture):
    coverage, _ = prepare_reservation(
        fixture.request, fixture.claims, (), observed_at=fixture.now
    )
    with pytest.raises(ValueError):
        checked(coverage.model_copy(update={"risk_amount": D("0.0001")}), RiskCoverage)
    receipt = ReservationReceipt(
        scope=fixture.request.scope,
        reservation_id="b" * 64,
        original_event_key=fixture.request.origin.original_event_key,
        report_id=fixture.request.origin.candidate.report_id,
        instrument_id=fixture.request.origin.evidence.pre_evidence.prefix.intent.instrument_id,
        direction=fixture.request.origin.candidate.direction,
        correlation_group="group",
        request_sha256=digest(fixture.request),
        coverage=coverage,
        state="uncertain",
        state_revision=2,
        account_revision=1,
        ledger_revision=3,
        created_at=fixture.now,
        updated_at=fixture.now,
        deadline=fixture.request.origin.deadline,
    )
    state = LedgerScopeState(
        scope=fixture.request.scope,
        account_revision=1,
        ledger_revision=3,
        claims_sha256=digest(fixture.claims),
        active=(receipt,),
    )
    assert checked(state, LedgerScopeState).active[0].state == "uncertain"
    with pytest.raises(ValueError):
        checked(state.model_copy(update={"active": iter((receipt,))}), LedgerScopeState)


def test_quote_nested_custom_serializer_never_runs(fixture):
    class Trap(LedgerScope):
        @model_serializer
        def serialize(self):
            pytest.fail("quote scalar serializer must not run")

    trap = Trap(account_id="123", settlement_currency="USDT")
    quote = fixture.request.quote.model_copy(update={"bid": trap})
    request = fixture.request.model_copy(update={"quote": quote})
    with pytest.raises(QualificationLedgerError, match="exact_quote_scalars_required"):
        checked(request, ReservationRequest)


def test_duplicate_pending_rejected_before_storage_and_before_self_removal(fixture):
    pending = reservation()
    claims = fixture.claims.model_copy(
        update={
            "account": fixture.claims.account.model_copy(
                update={
                    "pending_reservations": (pending, pending),
                    "pending_reservation_count": 2,
                },
            )
        }
    )
    with pytest.raises(QualificationLedgerError, match="duplicate_claimed_reservation"):
        validate_claims(claims, fixture.now)
    with pytest.raises(QualificationLedgerError, match="duplicate_claimed_reservation"):
        QualificationLedgerRepository._without_self(claims, None)


@pytest.mark.parametrize("field", tuple(module._QUOTE_SCALARS))
def test_quote_opaque_class_property_never_runs(fixture, field):
    calls = []

    class Opaque:
        @property
        def __class__(self):
            calls.append("class")
            return object

    quote = fixture.request.quote.model_copy(update={field: Opaque()})
    request = fixture.request.model_copy(update={"quote": quote})
    with pytest.raises(QualificationLedgerError, match="exact_quote_scalars_required"):
        checked(request, ReservationRequest)
    assert calls == []


def test_quote_foreign_metaclass_is_not_hashed_compared_or_inspected(fixture):
    calls = []

    class ForeignMeta(type):
        def __hash__(cls):
            calls.append("hash")
            return 0

        def __eq__(cls, other):
            calls.append("equality")
            return False

        def __getattribute__(cls, name):
            calls.append("class_attribute")
            return super().__getattribute__(name)

    class Opaque(metaclass=ForeignMeta):
        @property
        def __class__(self):
            calls.append("instance_class")
            return object

    quote = fixture.request.quote.model_copy(update={"bid": Opaque()})
    request = fixture.request.model_copy(update={"quote": quote})
    calls.clear()
    with pytest.raises(QualificationLedgerError, match="exact_quote_scalars_required"):
        checked(request, ReservationRequest)
    assert calls == []


@pytest.mark.parametrize("field", ("report_id", "bid", "quote_time"))
def test_quote_scalar_subclass_rejected_without_callback(fixture, field):
    calls = []

    class ForeignStr(str):
        @property
        def __class__(self):
            calls.append("str_class")
            return str

    class ForeignDecimal(Decimal):
        def is_finite(self):
            calls.append("is_finite")
            return True

    class ForeignDatetime(datetime):
        @property
        def tzinfo(self):
            calls.append("tzinfo")
            return UTC

    values = {
        "report_id": ForeignStr("synthetic"),
        "bid": ForeignDecimal("1"),
        "quote_time": ForeignDatetime(2026, 1, 1, tzinfo=UTC),
    }
    quote = fixture.request.quote.model_copy(update={field: values[field]})
    request = fixture.request.model_copy(update={"quote": quote})
    calls.clear()
    with pytest.raises(QualificationLedgerError, match="exact_quote_scalars_required"):
        checked(request, ReservationRequest)
    assert calls == []


@pytest.mark.parametrize(
    "field",
    (
        "quote_time",
        "mark_time",
        "funding_time",
        "received_at",
        "request_started_at",
    ),
)
def test_quote_foreign_timezone_rejected_before_offset_or_metaclass_callback(
    fixture, field
):
    calls = []

    class ZoneMeta(type):
        def __hash__(cls):
            calls.append("zone_type_hash")
            return 0

        def __eq__(cls, other):
            calls.append("zone_type_equality")
            return False

    class ForeignZone(tzinfo, metaclass=ZoneMeta):
        def utcoffset(self, at):
            calls.append("utcoffset")
            return timedelta(0)

        def dst(self, at):
            calls.append("dst")
            return timedelta(0)

        def tzname(self, at):
            calls.append("tzname")
            return "untrusted"

    clock = getattr(fixture.request.quote, field).replace(tzinfo=ForeignZone())
    quote = fixture.request.quote.model_copy(update={field: clock})
    request = fixture.request.model_copy(update={"quote": quote})
    calls.clear()
    with pytest.raises(QualificationLedgerError, match="exact_quote_timezone_required"):
        checked(request, ReservationRequest)
    assert calls == []


@pytest.mark.parametrize(
    "metadata",
    (
        "__pydantic_extra__",
        "__pydantic_private__",
        "__pydantic_fields_set__",
        "__dict__",
    ),
)
def test_quote_hidden_metadata_is_rejected_without_truthiness_or_iteration(
    fixture, metadata
):
    calls = []

    class Opaque:
        def __bool__(self):
            calls.append("bool")
            return False

        def __iter__(self):
            calls.append("iter")
            return iter(())

        @property
        def __class__(self):
            calls.append("class")
            return object

    class ForeignDict(dict):
        def __iter__(self):
            calls.append("dict_iter")
            return super().__iter__()

        def items(self):
            calls.append("items")
            return super().items()

        def __len__(self):
            calls.append("len")
            return super().__len__()

    quote = fixture.request.quote.model_copy()
    value = ForeignDict(quote.__dict__) if metadata == "__dict__" else Opaque()
    object.__setattr__(quote, metadata, value)
    request = fixture.request.model_copy(update={"quote": quote})
    calls.clear()
    with pytest.raises(QualificationLedgerError, match="dirty_quote_contract"):
        checked(request, ReservationRequest)
    assert calls == []


@pytest.mark.parametrize("zone_kind", ("fixed_offset", "UTC", "Asia/Taipei"))
def test_quote_standard_timezones_preserve_utc_normalized_roundtrip(fixture, zone_kind):
    if zone_kind == "fixed_offset":
        zone = timezone(timedelta(hours=8))
    else:
        zone = ZoneInfo(zone_kind)
    changes = {
        name: getattr(fixture.request.quote, name).astimezone(zone)
        for name, expected in module._QUOTE_SCALARS.items()
        if expected is datetime
    }
    request = fixture.request.model_copy(
        update={
            "quote": fixture.request.quote.model_copy(update=changes),
        }
    )
    result = checked(request, ReservationRequest)
    assert result == fixture.request
    assert decode(canonical(result), ReservationRequest) == fixture.request


@pytest.mark.parametrize("value", ("NaN", "sNaN", "Infinity", "-Infinity", "1e999"))
def test_quote_exact_decimal_still_requires_finite_bounded_value(fixture, value):
    request = fixture.request.model_copy(
        update={
            "quote": fixture.request.quote.model_copy(update={"bid": D(value)}),
        }
    )
    with pytest.raises(ValueError):
        checked(request, ReservationRequest)
