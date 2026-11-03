"""Raw synthetic V2 family mathematics, never PG/native/account acceptance.

The existing fixtures build coherent raw child/parent candles and declared
funding bytes BEFORE capture. No observed source timestamp, packet, history
verdict, namespace guard, source owner, issuer, reserve or submit is patched.
Every constructed current/account/local inspection below is explicitly DATA.
No synthetic claims are sent to SQL and no positive native owner is invented.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Context, Decimal, localcontext

import pytest

from app.database.repositories.qualification_ledger import (
    _ConsumedEventJournalInspection,
)
from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.domain.source_primitives import canonical, decode
from app.strategies import structural_protection as selection
from app.trade_qualification import account_capture, data_v2
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification import current_conditions as conditions
from app.trade_qualification import fixed_protection as fixed
from app.trade_qualification import native_original_bracket_policy_v1 as policy
from app.trade_qualification import original_candidate_precursor_v2 as precursor
from app.trade_qualification import owned_current_original_prefix_v1 as local
from app.trade_qualification import sweep_contract as sweep
from app.trade_qualification.account_observation_index import CaptureReference
from app.trade_qualification.native_original_g1_policy_v1 import (
    DATA_POLICY_SHA256,
    fixed_native_original_g1_policy,
)
from tests.unit import test_original_candidate_policy as account_fixture
from tests.unit import test_original_candidate_precursor_v2 as raw_fixture
from tests.unit.research import test_owned_current_original_prefix_v1 as base_fixture

HISTORY_CASES = raw_fixture.HISTORY_CASES


def _owned_adverse_full_bracket_raw_source_v3(strategy, direction):
    """A distinct coherent synthetic source, fully declared BEFORE raw capture.

    The older G1-G5 scenario remains adverse and is tested below. This source
    declares actual structural room and a live latest momentum transition; no
    collected receipt, price, clock, threshold or selector output is repaired.
    """
    source = raw_fixture.history_raw_source(strategy, direction)
    market = source.market.model_copy(deep=True)
    original_timestamps = {
        frame: tuple(row.timestamp for row in rows)
        for frame, rows in market.candles.items()
    }
    long = direction == "long"
    now = raw_fixture.NOW.replace(second=0, microsecond=0)

    def extreme(row, price, *, opposing):
        field = "high" if opposing == long else "low"
        return row.model_copy(update={field: price})

    def reflected(price):
        return Decimal(price) if long else Decimal(200) - Decimal(price)

    def amend(rows, timestamp, price, *, opposing):
        matches = [
            index for index, row in enumerate(rows) if row.timestamp == timestamp
        ]
        assert len(matches) == 1
        index = matches[0]
        rows[index] = extreme(rows[index], price, opposing=opposing)

    if strategy == "structure_reversal":
        rows = market.candles["1H"]
        closes = tuple(row.close for row in rows)
        # The old smooth-close decline had disjoint HTF wicks, creating actual
        # bearish/bullish FVGs containing the candidate. Declare overlap in its
        # earlier raw tape, retaining all closes and the last seven native bars.
        for index in range(len(rows) - 60, len(rows) - 7):
            neighbours = closes[max(0, index - 2) : index + 3]
            rows[index] = rows[index].model_copy(
                update={
                    "high": max(neighbours) + Decimal("0.20"),
                    "low": min(neighbours) - Decimal("0.20"),
                }
            )
        fifteen = market.candles["15m"]
        raw_fixture._seed_covered_children(
            rows,
            fifteen,
            3600,
            900,
            keep=range(len(fifteen) - 28, len(fifteen)),
        )
        # A parent's extreme need not occur in its first child. Place this
        # latest parent's original extreme in its LAST child, preserving exact
        # parent OHLC; this distinct scenario has no earlier confirmed 15m pivot
        # at that extreme. The original first-child scenario stays adverse.
        latest = rows[-1]
        indices = [
            index
            for index, row in enumerate(fifteen)
            if latest.timestamp <= row.timestamp < latest.timestamp + timedelta(hours=1)
        ]
        assert len(indices) == 4
        field = "high" if long else "low"
        parent_extreme = getattr(latest, field)
        for index in indices[:-1]:
            row = fifteen[index]
            if getattr(row, field) == parent_extreme:
                replacement = (
                    max(row.open, row.close) + Decimal("0.01")
                    if long
                    else min(row.open, row.close) - Decimal("0.01")
                )
                fifteen[index] = row.model_copy(update={field: replacement})
        last = fifteen[indices[-1]]
        fifteen[indices[-1]] = last.model_copy(update={field: parent_extreme})
        five = market.candles["5m"]
        original_closes = tuple(row.close for row in five)
        raw_fixture._seed_covered_children(
            fifteen,
            five,
            900,
            300,
            keep=range(len(five) - 8, len(five)),
        )
        # Preserve the original late dip/false-to-true momentum closes. Only
        # propagate the newly located parent's extreme to its first 5m child.
        indices = [
            index for index, row in enumerate(five) if row.timestamp == last.timestamp
        ]
        assert len(indices) == 1
        row = five[indices[0]]
        five[indices[0]] = row.model_copy(update={field: parent_extreme})
        assert tuple(row.close for row in five) == original_closes
    elif strategy == "volatility_expansion":
        # The existing latest compression/break and every recent close/wick
        # remain exact. Declare old confirmed anchors in the captured 1H tape,
        # far outside the recent ATR window; rebuild their actual 4H parents.
        # R3's recent new wicks had correctly caused high-HTF-volatility denial.
        target_at = now.replace(hour=12, minute=0) - timedelta(days=9)
        stop_at = now.replace(hour=12, minute=0) - timedelta(days=8)
        amend(market.candles["1H"], target_at, reflected("110"), opposing=True)
        amend(market.candles["1H"], stop_at, reflected("98"), opposing=False)
    elif strategy == "range_reversal":
        # R3's single 105 wick erased the original approach/touch through ATR.
        # Declare a small monotone high/low edge instead of a confirmed nearby
        # pivot, retaining the original earlier 105/95 boundary and setup lows.
        five = market.candles["5m"]
        pivot = market.candles["15m"][-6]
        for offset in (0, 1):
            stamp = pivot.timestamp + timedelta(minutes=5 * offset)
            matches = [
                index for index, row in enumerate(five) if row.timestamp == stamp
            ]
            assert len(matches) == 1
            row = five[matches[0]]
            close = reflected("100.10")
            five[matches[0]] = row.model_copy(
                update={
                    "open": close,
                    "close": close,
                    "high": close + Decimal("0.01"),
                    "low": close - Decimal("0.01"),
                }
            )
        for offset, parent in enumerate(market.candles["15m"][-6:]):
            edge = Decimal("100.15") + Decimal(offset) * Decimal("0.01")
            amend(five, parent.timestamp, reflected(edge), opposing=True)
    elif strategy == "liquidity_sweep_reversal":
        # This R3 branch remains an explicitly adverse pending fixture: its
        # latest trigger is correct but coherent HTF neutrality is not proven.
        # The observed old transition expires exactly at 10:10. Declare a NEW
        # final closed raw pair, preserving the unchanged final quote/close.
        # The preceding directional pulse and final pullback cross the actual
        # MACD/RSI momentum predicate; no old trigger receives another expiry.
        rows = market.candles["5m"]
        prior = rows[-2]
        close = reflected("101.7")
        rows[-2] = prior.model_copy(
            update={
                "open": close,
                "close": close,
                "high": close + Decimal("0.01"),
                "low": close - Decimal("0.01"),
            }
        )
        resistance = market.candles["15m"][-20]
        amend(rows, resistance.timestamp, reflected("115"), opposing=True)
    else:
        raise ValueError("explicit_owned_history_family_fixture_required")
    # Rebuild actual child/parent OHLC and both SWAP turnover sums before the
    # unchanged collector serves any bytes. No timestamp is changed here.
    rebuilt = raw_fixture.v2_market(market)
    assert all(
        tuple(row.timestamp for row in rebuilt.candles[frame]) == stamps
        for frame, stamps in original_timestamps.items()
    )
    assert rebuilt.received_at == source.market.received_at
    assert rebuilt.ticker == source.market.ticker
    assert rebuilt.order_book == source.market.order_book
    assert rebuilt.mark_price == source.market.mark_price
    return replace(source, market=rebuilt)


def _need(condition):
    """Local synthetic-input invariant only; no native or gate authority."""
    if condition is not True:
        raise ValueError("common_sweep_synthetic_input_invalid") from None


def _common_sweep_raw_source_v4(
    peak_text, decline_hours, direction, *, corrected=False, profile="R7"
):
    """Deterministic public input only. No indicator/permission enters this API."""
    source = raw_fixture.sweep_v6_source(direction, "inside")
    market = source.market.model_copy(deep=True)
    now, observed = raw_fixture.NOW, raw_fixture.OBSERVED
    shift = now - observed
    _need(shift.seconds == 0 and shift.microseconds == 0)
    peak = Decimal(peak_text)
    step = timedelta(minutes=5)
    templates = market.candles
    stamps = {
        tf: tuple(row.timestamp + shift for row in rows)
        for tf, rows in templates.items()
    }
    first = min(values[0] for values in stamps.values())
    count = (now - first) // step
    _need(10000 <= count <= 12000 and first + count * step == now)

    def price(at):
        age = Decimal((at - now) // timedelta(seconds=1)) / Decimal(3600)
        if corrected and profile in {"R8", "R9", "R10"}:
            # A fixed NEW declared common tape, not changes to an old capture.
            # A shorter recovery supplies EMA20/50 separation, while decline
            # continues until the native pattern begins (no artificial plateau).
            if age <= -340:
                value = Decimal(80)
            elif age < -300:
                value = Decimal(80) + (age + Decimal(340)) * Decimal(30) / Decimal(40)
            elif age < -180:
                value = Decimal(110)
            elif age < -76:
                value = Decimal(110) - (age + Decimal(180)) * Decimal(20) / Decimal(104)
            elif age < -16:
                value = Decimal(90) + (age + Decimal(76)) * Decimal("9.5") / Decimal(60)
            elif profile == "R10" and age < -6:
                # ONE new late-history hypothesis: retain the older warmup,
                # then a late-accelerating decline into a six-hour-old low.
                # A cubic path retains more of the earlier recovery than a
                # linear dump; no EMA/derivative/coverage value is supplied.
                progress = (age + Decimal(16)) / Decimal(10)
                value = Decimal("99.5") - Decimal(5) * progress**3
            elif profile == "R10" and age < Decimal("-1.75"):
                # Recover from that actual low into the unchanged native
                # seven-bar pattern's original first open (mapped to97.68).
                value = Decimal("94.5") + (age + Decimal(6)) * Decimal(
                    "0.86"
                ) / Decimal("4.25")
            elif profile == "R10":
                value = Decimal("95.36")
            elif age < Decimal("-1.75"):
                value = Decimal("99.5") - (age + Decimal(16)) * Decimal(4) / Decimal(
                    "14.25"
                )
            else:
                value = Decimal("95.5")
        elif corrected:
            # One fixed NEW common source: different EMA horizons arise from
            # the same history, with a genuine rising HTF recovery rather than
            # the prior flat plateau followed only by an opposed decline.
            if age <= -340:
                value = Decimal(80)
            elif age < -300:
                value = Decimal(80) + (age + Decimal(340)) * Decimal(30) / Decimal(40)
            elif age < -180:
                value = Decimal(110)
            elif age < -100:
                value = Decimal(110) - (age + Decimal(180)) * Decimal(20) / Decimal(80)
            elif age < -16:
                value = Decimal(90) + (age + Decimal(100)) * Decimal("9.5") / Decimal(
                    84
                )
            elif age < -4:
                value = Decimal("99.5") - (age + Decimal(16)) * Decimal(4) / Decimal(12)
            else:
                value = Decimal("95.5")
        elif age <= -480:
            value = Decimal(110)
        elif age < -240:
            value = Decimal(110) - (age + Decimal(480)) * Decimal(20) / Decimal(240)
        elif age < -100:
            value = Decimal(90)
        elif age < -decline_hours:
            value = peak
        elif age < -2:
            value = peak + (Decimal("95.5") - peak) * (
                age + Decimal(decline_hours)
            ) / Decimal(decline_hours - 2)
        else:
            value = Decimal("95.5")
        return value.quantize(Decimal("0.00000001"))

    def reflected(value):
        if corrected and profile in {"R8", "R9", "R10"}:
            # This one predeclared affine input map applies to EVERY common
            # child price and hence every parent OHLC, mark and quote. It does
            # not change the ATR policy or restamp a retained source.
            value = Decimal(100) + (value - Decimal(100)) * Decimal("0.5")
        return value if direction == "long" else Decimal(200) - value

    def falling(at):
        age = (at - now) // timedelta(seconds=1)
        if corrected and profile == "R10":
            return -180 * 3600 < age <= -76 * 3600 or -16 * 3600 < age <= -6 * 3600
        if corrected and profile in {"R8", "R9", "R10"}:
            return -180 * 3600 < age <= -76 * 3600 or -16 * 3600 < age <= -6300
        if corrected:
            return -180 * 3600 < age <= -100 * 3600 or -16 * 3600 < age <= -4 * 3600
        return (
            -480 * 3600 < age <= -240 * 3600 or -decline_hours * 3600 < age <= -2 * 3600
        )

    rows, by_open = [], {}
    default = templates["5m"][0]
    with localcontext(Context(prec=100)):
        for index in range(count):
            stamp = first + index * step
            end = stamp + step
            opened, closed = price(stamp), price(end)
            high, low = (
                max(opened, closed) + Decimal("0.01"),
                min(opened, closed) - Decimal("0.01"),
            )
            # Lower wick witnesses suppress opposing FVGs in a monotone decline
            # without inventing a nearer swing-high target. A parent-boundary
            # impulse is an actual 5m wick and propagates into every parent.
            if falling(end):
                epoch_seconds = (
                    end - raw_fixture.datetime(1970, 1, 1, tzinfo=raw_fixture.UTC)
                ) // timedelta(seconds=1)
                for seconds in (900, 3600, 14400):
                    if epoch_seconds % seconds == 0:
                        distance = price(end - timedelta(seconds=seconds)) - closed
                        if distance > 0:
                            low = min(low, closed - distance - Decimal("0.01"))
            if stamp == now - timedelta(hours=600):
                high = Decimal(
                    112
                )  # Distant confirmed original target, outside recent ATR.
            if corrected and stamp == now - timedelta(hours=58):
                # This actual old 5m wick supplies the missing confirmed 15m
                # resistance above current price, outside the recent ATR window.
                high = Decimal(105)
            row = default.model_copy(
                update={
                    "timestamp": stamp,
                    "open": reflected(opened),
                    "close": reflected(closed),
                    "high": reflected(high) if direction == "long" else reflected(low),
                    "low": reflected(low) if direction == "long" else reflected(high),
                    "volume_quote": default.volume_currency * reflected(closed),
                    "confirmed": True,
                }
            )
            by_open[stamp] = len(rows)
            rows.append(row)
        # Keep one unchanged native event pattern, translated as a NEW declared
        # scenario. Seven recent15m rows carry the actual sweep/reclaim/CHoCH.
        native = []
        delta = Decimal("-5.5") if direction == "long" else Decimal("5.5")
        for row in templates["15m"][-7:]:
            values = {
                name: getattr(row, name) + delta
                for name in ("open", "high", "low", "close")
            }
            if corrected:
                anchor = Decimal("95.5") if direction == "long" else Decimal("104.5")
                # A predeclared affine raw-price scenario keeps the same
                # sweep/reclaim/co-confirmed structure chronology, with a
                # smaller observed jump into its containing 1H candle.
                values = {
                    name: anchor + (value - anchor) * Decimal("0.2")
                    for name, value in values.items()
                }
                if profile in {"R8", "R9", "R10"}:
                    values = {
                        name: Decimal(100) + (value - Decimal(100)) * Decimal("0.5")
                        for name, value in values.items()
                    }
            native.append(
                row.model_copy(
                    update={
                        "timestamp": row.timestamp + shift,
                        **values,
                    }
                )
            )
        raw_fixture._seed_covered_children(native, rows, 900, 300)
        for offset, value in (
            (-2, Decimal("101.7") + Decimal("-5.5")),
            (-1, Decimal("101.4") + Decimal("-5.5")),
        ):
            stamp = stamps["5m"][offset]
            index = by_open[stamp]
            if corrected:
                value = Decimal("95.5") + (value - Decimal("95.5")) * Decimal("0.2")
            value = reflected(value)
            if corrected and profile in {"R9", "R10"}:
                # ONE new pre-capture pair: a directional overshoot followed
                # by a smaller pullback. The old R8 true/true pair remains
                # exact under profile R8 and is a regression below. These two
                # raw bars open after the stored 15m/1H/4H cutoff, so every
                # already-closed parent and the original setup remain exact.
                value = Decimal("97.90") if offset == -2 else Decimal("97.85")
                value = value if direction == "long" else Decimal(200) - value
            row = rows[index]
            rows[index] = row.model_copy(
                update={
                    "open": value,
                    "close": value,
                    "high": value + Decimal("0.01"),
                    "low": value - Decimal("0.01"),
                }
            )
        # Actual source frames are exact finite slices/sums of the SAME tape.
        rebuilt = {}
        for tf, template in templates.items():
            width = raw_fixture.BAR_SECONDS[tf] // 300
            output = []
            for original, stamp in zip(template, stamps[tf], strict=True):
                index = by_open[stamp]
                children = rows[index : index + width]
                _need(len(children) == width)
                output.append(
                    original.model_copy(
                        update={
                            "timestamp": stamp,
                            "open": children[0].open,
                            "high": max(row.high for row in children),
                            "low": min(row.low for row in children),
                            "close": children[-1].close,
                            "volume_contracts": sum(
                                (row.volume_contracts for row in children), Decimal(0)
                            ),
                            "volume_currency": sum(
                                (row.volume_currency for row in children), Decimal(0)
                            ),
                            "volume_quote": sum(
                                (row.volume_currency * row.close for row in children),
                                Decimal(0),
                            ),
                            "confirmed": True,
                        }
                    )
                )
            rebuilt[tf] = output
    market.candles = rebuilt
    final = rows[by_open[stamps["5m"][-1]]].close
    market.received_at = now
    market.ticker = market.ticker.model_copy(
        update={
            "last": final,
            "bid": final - Decimal("0.01"),
            "ask": final + Decimal("0.01"),
            "open_24h": final,
            "high_24h": final + Decimal(1),
            "low_24h": final - Decimal(1),
            "timestamp": now,
        }
    )
    market.order_book = market.order_book.model_copy(
        update={
            "timestamp": now,
            "bids": [
                row.model_copy(update={"price": final - Decimal("0.01")})
                for row in market.order_book.bids
            ],
            "asks": [
                row.model_copy(update={"price": final + Decimal("0.01")})
                for row in market.order_book.asks
            ],
        }
    )
    market.mark_price = final
    if market.next_funding_time is not None:
        market.next_funding_time += shift
    market = raw_fixture.v2_market(market)
    # v2_market converts the ticker but initializes remote parent turnovers as
    # volume*close. Restore every source frame's exact SAME-tape quote sum,
    # including parents outside the child frames' shorter retained slices.
    with localcontext(Context(prec=100)):
        for tf, output in market.candles.items():
            width = raw_fixture.BAR_SECONDS[tf] // 300
            for row in output:
                index = by_open[row.timestamp]
                row.volume_quote = sum(
                    (
                        child.volume_currency * child.close
                        for child in rows[index : index + width]
                    ),
                    Decimal(0),
                )
    return replace(source, market=market, evaluated_at=now + timedelta(milliseconds=10))


def _owned_full_bracket_raw_source_v3(strategy, direction):
    if strategy == "liquidity_sweep_reversal":
        return _common_sweep_raw_source_v4(
            "99.5", 16, direction, corrected=True, profile="R10"
        )
    source = _owned_adverse_full_bracket_raw_source_v3(strategy, direction)
    if strategy != "range_reversal":
        return source
    # A NEW declared executable-quote scenario before the raw capture. The
    # earlier exact R4 source remains adverse below: its ask/bid is one tick
    # beyond the original zone. No old packet, quote or zone is rewritten.
    market = source.market.model_copy(deep=True)
    bid, ask = (
        (Decimal("100.08"), Decimal("100.10"))
        if direction == "long"
        else (Decimal("99.90"), Decimal("99.92"))
    )
    market.ticker = market.ticker.model_copy(update={"bid": bid, "ask": ask})
    market.order_book = market.order_book.model_copy(
        update={
            "bids": [
                row.model_copy(update={"price": bid}) for row in market.order_book.bids
            ],
            "asks": [
                row.model_copy(update={"price": ask}) for row in market.order_book.asks
            ],
        }
    )
    assert market.candles == source.market.candles
    assert market.mark_price == source.market.mark_price
    assert market.received_at == source.market.received_at
    assert market.ticker.timestamp == source.market.ticker.timestamp
    assert market.order_book.timestamp == source.market.order_book.timestamp
    return replace(source, market=market)


def _capture_raw_family_values_v3(source, strategy, root):
    diagnostic = raw_fixture.capture_raw_source(
        source,
        root,
        declared_funding=raw_fixture.declared_history_funding_row(source),
    )
    packet = account_fixture.account_source()
    args = raw_fixture.inputs(
        diagnostic,
        packet,
        strategy=strategy,
        data_policy=fixed_native_original_g1_policy(),
        expected_data_policy_sha256=DATA_POLICY_SHA256,
    )
    original = precursor.derive_owned_original_candidate_precursor_v3(
        diagnostic.packet, packet, **args
    )
    return diagnostic, packet, args, original


def _family_replay_values_v3(strategy, direction, diagnostic, packet, args, original):
    data_policy = fixed_native_original_g1_policy()
    frozen = account_capture.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    private = current.RecordedCurrentAccountBundleV4(
        packet,
        CaptureReference(
            capture_id="1" * 32,
            head_sha256="2" * 64,
            plan_sha256=packet.plan_sha256,
            packet_sha256=frozen.sha256,
            session_binding_sha256="3" * 64,
        ),
        b"{}",
        "4" * 64,
        "5" * 64,
        "6" * 64,
        args["service_deadline"],
        1,
    )
    assert private.execution_authority is False
    g1 = data_v2.evaluate_public_market_data_v2(
        diagnostic.packet,
        expected_bundle_sha256=diagnostic.packet.bundle_sha256,
        policy=data_policy,
        evaluated_at=args["created_at"],
    )
    g1 = data_v2.verify_public_market_data_v2(
        g1,
        diagnostic.packet,
        expected_bundle_sha256=diagnostic.packet.bundle_sha256,
        policy=data_policy,
        evaluated_at=args["created_at"],
    )
    assert g1.passed
    replay = (
        diagnostic.packet,
        private,
        diagnostic.context,
        g1,
        original,
        args["created_at"],
        args["service_deadline"],
    )
    computed = local._compute_owned_family_v2(replay)
    return strategy, direction, diagnostic, packet, args, replay, computed


@pytest.fixture(scope="module", params=HISTORY_CASES, ids=lambda value: "-".join(value))
def raw_family(request, tmp_path_factory):
    strategy, direction = request.param
    source = _owned_full_bracket_raw_source_v3(strategy, direction)
    diagnostic, packet, args, original = _capture_raw_family_values_v3(
        source,
        strategy,
        tmp_path_factory.mktemp("owned-full-bracket-v3"),
    )
    assert original.intent is not None, decode(original.receipt_json)["code"]
    return _family_replay_values_v3(
        strategy, direction, diagnostic, packet, args, original
    )


def _pure_local(raw_family):
    strategy, _, _diagnostic, _, _, replay, computed = raw_family
    packet, private, context, _, _, created, _ = replay
    captured_policy = local._freeze_owned_family_policy_v2(strategy)
    protection = local.ProtectionPolicy.model_validate_json(
        canonical(decode(captured_policy)["protection"]), strict=True
    )
    source = decode(computed.g1.source_json.encode())
    market = MarketSnapshot.model_validate_json(
        canonical(source["market"]), strict=True
    )
    analysis = MultiTimeframeAnalysis.model_validate_json(
        canonical(source["analysis"]), strict=True
    )
    rules = local.derive_captured_instrument_rules(
        private.packet,
        instrument_id=computed.intent.instrument_id,
        expected_plan_sha256=private.packet.plan_sha256,
        expected_packet_sha256=private.reference.packet_sha256,
    )
    selected = local._select_owned_family_protection_v2(
        computed.detection,
        market,
        analysis,
        observed_at=created,
        entry=computed.intent.candidate_entry,
        tick_size=rules.tick_size,
        **{
            key: value
            for key, value in protection.model_dump().items()
            if key != "policy_id"
        },
    )
    record = {
        "computed": computed,
        "public_packet": packet,
        "quote_value": context.quote,
        "quote_context": local._replay_local_quote_v13(
            packet, context.quote, computed, evaluated_at=created
        ),
        "market": market,
        "analysis": analysis,
        "protection": protection,
        "policy_raw": captured_policy,
        "tick_size": rules.tick_size,
        "created_at": created,
        "selection_raw": selected.to_audit_json(),
    }
    journal = _ConsumedEventJournalInspection(
        frozenset(), canonical({"ledger_revision": 0}), created, created
    )
    assert journal.execution_authority is False
    return record, journal, created, selected


def test_actual_raw_family_prefix_replays_original_history_without_an_owner(raw_family):
    strategy, direction, diagnostic, packet, args, replay, computed = raw_family
    before = (len(local._LOCAL_HOLDS_V13), len(local._LOCAL_OWNERS_V13))
    original = replay[4]
    assert (
        precursor.verify_owned_original_candidate_precursor_v3(
            original, diagnostic.packet, packet, **args
        )
        == original
    )
    assert isinstance(computed, local.CurrentOriginalFamilyPrefixInputsV2)
    assert len(computed.result.gates) == 5 and all(
        gate.passed for gate in computed.result.gates
    )
    assert (
        computed.intent.strategy == strategy and computed.intent.direction == direction
    )
    assert computed.result.execution_authority is False
    assert computed.execution_authority is False
    assert computed.result.history_admission_sha256 is not None
    if strategy == "liquidity_sweep_reversal":
        assert computed.detection.trigger.trigger_time == raw_fixture.NOW
        assert computed.detection.trigger.expires_at == raw_fixture.NOW + timedelta(
            seconds=300
        )
        assert args["created_at"] < computed.detection.trigger.expires_at
    history = decode(computed.family_history_json)
    assert history["original_event_key"] == local.event_identity(computed.detection)
    assert history["original_source_sha256"] == computed.g1.source_sha256
    assert history["original_observed_at"] == computed.detection.observed_at.isoformat()
    assert history["execution_authority"] is False
    assert (len(local._LOCAL_HOLDS_V13), len(local._LOCAL_OWNERS_V13)) == before
    # Historical base contract is still closed to this new family.
    with pytest.raises(local.CurrentOriginalPrefixError):
        local._compute(replay)


def test_history_classification_does_not_relabel_the_raw_router(raw_family):
    strategy, _, _, _, _, _, computed = raw_family
    measured = computed.result.gates[1].measured_values
    assert measured["router_relabelled"] is False
    if strategy == "liquidity_sweep_reversal":
        assert measured["actual_router_regime"] == "Unknown"
        assert computed.result.market_regime == "History Verified Sweep"
    assert (
        local.decode_owned_family_result_v2(
            computed.result.model_dump(mode="json", round_trip=True)
        )
        == computed.result
    )


@pytest.mark.parametrize(
    "field",
    ["event_key", "timeline_sha256", "event_prefix_witness", "history_admission"],
)
def test_self_signed_original_receipt_cannot_replace_actual_raw_history(
    raw_family, field
):
    _, _, diagnostic, packet, args, replay, _ = raw_family
    original = replay[4]
    doc = decode(original.receipt_json)
    if field == "history_admission":
        # A range receipt legitimately has no separate admission object. None
        # over None is no mutation; forge a real different field in every case.
        admission = doc[field]
        doc[field] = (
            {"admitted": True}
            if admission is None
            else {**admission, "source_sha256": "0" * 64}
        )
    else:
        doc[field] = None if field != "event_key" else "0" * 64
    changed = canonical(doc)
    assert changed != original.receipt_json
    forged = replace(original, receipt_json=changed)
    with pytest.raises(precursor.OriginalCandidatePrecursorV2Error):
        precursor.verify_owned_original_candidate_precursor_v3(
            forged, diagnostic.packet, packet, **args
        )


@pytest.mark.parametrize(
    "field", ["source_policy", "history_admission", "execution_authority"]
)
def test_family_result_decode_cannot_accept_extra_truth_fields(raw_family, field):
    computed = raw_family[-1]
    doc = computed.result.model_dump(mode="json", round_trip=True)
    doc[field] = True
    with pytest.raises((ValueError, local.CurrentOriginalPrefixError)):
        local.decode_owned_family_result_v2(doc)


def test_actual_family_selector_is_replayable_without_price_repair(raw_family):
    record, journal, at, selected = _pure_local(raw_family)
    assert selected.strategy == record["computed"].intent.strategy
    again = local._select_owned_family_protection_v2(
        record["computed"].detection,
        record["market"],
        record["analysis"],
        observed_at=at,
        entry=record["computed"].intent.candidate_entry,
        tick_size=record["tick_size"],
        **{
            key: value
            for key, value in record["protection"].model_dump().items()
            if key != "policy_id"
        },
    )
    assert again.to_audit_json() == selected.to_audit_json()
    assert selected.execution_authority is False
    if raw_family[0] == "liquidity_sweep_reversal":
        assert type(selected) is selection.SweepStructuralProtectionSelectionV2
        assert (
            selected.sweep_permission_policy_sha256
            == sweep.SWEEP_PERMISSION_SWAP_V2_SHA256
        )
        assert selected.selection_policy_sha256 == sweep.SWEEP_SELECTION_SWAP_V2_SHA256
    result = local._evaluate_owned_family_local_math_v2(record, journal, observed_at=at)
    assert result.trigger == record["computed"].detection.trigger
    assert result.entry_zone == record["computed"].original_zone
    assert result.candidate_entry == record["computed"].intent.candidate_entry
    assert result.execution_authority is False
    assert len(result.gates) <= 10  # This pure test does not manufacture G11.
    assert not any(gate.gate == local.QualificationGate.RISK for gate in result.gates)


def test_consumed_original_event_stops_at_real_g6_for_each_family(raw_family):
    record, journal, at, _ = _pure_local(raw_family)
    journal = _ConsumedEventJournalInspection(
        frozenset((local.event_identity(record["computed"].detection),)),
        journal.document_json,
        at,
        at,
    )
    result = local._evaluate_owned_family_local_math_v2(record, journal, observed_at=at)
    assert len(result.gates) == 6 and not result.gates[-1].passed
    assert result.stop_loss is None and result.take_profit is None
    assert result.execution_authority is False


def test_current_family_permission_reuses_fixed_original_history(raw_family):
    record, _, _at, _ = _pure_local(raw_family)
    computed = record["computed"]
    args = {
        "intent": computed.intent,
        "policy": local.QualificationPrefixPolicy(
            policy_id="owned-family-test",
            data=fixed_native_original_g1_policy(),
            minimum_score=85,
            tick_size=record["tick_size"],
            max_allowed_drift_bps=Decimal(30),
            maximum_strategy_spread_bps=Decimal(3),
            maximum_adverse_funding_bps=Decimal(5),
        ),
        "source_sha256": computed.g1.source_sha256,
        "quote_bundle_sha256": computed.g1.quote_bundle_sha256,
        "bid": record["quote_context"].quote.ticker.bid,
        "ask": record["quote_context"].quote.ticker.ask,
        "funding_rate": record["quote_context"].quote.funding.forecast.rate,
        "detection": computed.detection,
        "original_source_sha256": computed.g1.source_sha256,
        "original_history_json": computed.family_history_json,
    }
    gates, values = [], {}

    def gate(kind, code, reason, measured):
        gates.append((kind, code))
        return code == "passed"

    conditions._evaluate_owned_history_g2_g4_v2(
        record["market"], record["analysis"], values=values, gate=gate, **args
    )
    assert len(gates) == 3 and all(code == "passed" for _, code in gates)
    assert decode(computed.family_history_json)[
        "original_event_key"
    ] == local.event_identity(computed.detection)
    forged = decode(computed.family_history_json)
    forged["original_event_key"] = "0" * 64
    with pytest.raises(ValueError, match="original_receipt_changed"):
        conditions._evaluate_owned_history_g2_g4_v2(
            record["market"],
            record["analysis"],
            values={},
            gate=gate,
            **{**args, "original_history_json": canonical(forged)},
        )


def test_actual_current_fixed_protection_preserves_or_rejects_original_prices(
    raw_family,
):
    record, _, at, selected = _pure_local(raw_family)
    # A predeclared positive source must really reach this guard. No conditional
    # return/skip is allowed to turn a missing source bracket into test success.
    assert selected.protection_valid, selected.fail_codes
    bracket = selected.selected
    result = fixed.evaluate_owned_fixed_protection_v2(
        record["market"],
        record["analysis"],
        record["market"],
        record["analysis"],
        detection=record["computed"].detection,
        original_selection_audit_json=record["selection_raw"],
        entry=record["computed"].intent.candidate_entry,
        stop_loss=bracket.stop.final_stop,
        take_profit=bracket.target.final_target,
        policy=record["protection"],
        tick_size=record["tick_size"],
        data_policy=fixed_native_original_g1_policy(),
        quote=record["quote_context"].quote,
        observed_at=at,
    )
    assert result.passed, [(check.step, check.code) for check in result.checks]
    assert result.entry == record["computed"].intent.candidate_entry
    assert (
        result.stop_loss == bracket.stop.final_stop
        and result.take_profit == bracket.target.final_target
    )
    assert result.execution_authority is False
    assert result.source_authenticity_verified is False
    assert result.continuation_verified is False
    assert all(
        check.measured_values.get("prices_changed", False) is False
        for check in result.checks
    )


@pytest.mark.parametrize("strategy", precursor.STRATEGY_CATALOG)
def test_every_family_has_explicit_fixed_source_policy(strategy):
    value = decode(policy.fixed_native_owned_family_policy_v2(strategy))
    assert value["strategy"] == strategy
    assert value["execution_authority"] is False
    assert value["history_contract"] and value["selection_contract"]


def test_original_base_computation_and_selector_remain_exact(tmp_path_factory):
    replay = base_fixture._arithmetic_replay_values(
        tmp_path_factory, fresh_funding=True
    )
    computed = local._compute(replay)
    assert type(computed) is local.CurrentOriginalBasePrefixInputsV1
    assert type(computed.result) is local.EntryQualificationResult
    assert "family_history_json" not in computed.__dataclass_fields__
    source = decode(computed.g1.source_json.encode())
    market = MarketSnapshot.model_validate_json(
        canonical(source["market"]), strict=True
    )
    analysis = MultiTimeframeAnalysis.model_validate_json(
        canonical(source["analysis"]), strict=True
    )
    captured = decode(local._freeze_local_gate_policy_v13())
    kwargs = {
        key: Decimal(value)
        for key, value in captured["protection"].items()
        if key != "policy_id"
    }
    kwargs.update(
        observed_at=replay[5],
        entry=computed.intent.candidate_entry,
        tick_size=Decimal("0.01"),
    )
    old = selection.select_structural_protection(
        computed.detection, market, analysis, **kwargs
    )
    new = local._select_owned_family_protection_v2(
        computed.detection, market, analysis, **kwargs
    )
    assert old.to_audit_json() == new.to_audit_json()


def test_current_fixed_guard_rejects_changed_original_target_without_repair(raw_family):
    record, _, at, selected = _pure_local(raw_family)
    assert selected.protection_valid, selected.fail_codes
    bracket = selected.selected
    altered = bracket.target.final_target + record["tick_size"]
    result = fixed.evaluate_owned_fixed_protection_v2(
        record["market"],
        record["analysis"],
        record["market"],
        record["analysis"],
        detection=record["computed"].detection,
        original_selection_audit_json=record["selection_raw"],
        entry=record["computed"].intent.candidate_entry,
        stop_loss=bracket.stop.final_stop,
        take_profit=altered,
        policy=record["protection"],
        tick_size=record["tick_size"],
        data_policy=fixed_native_original_g1_policy(),
        quote=record["quote_context"].quote,
        observed_at=at,
    )
    assert not result.passed
    assert result.take_profit == altered  # Rejection never silently repairs it.
    assert result.execution_authority is False


def test_typed_family_evidence_and_historical_decode_are_data_only(raw_family):
    from app.trade_evidence import native_v2 as evidence

    record, journal, at, selected = _pure_local(raw_family)
    actual = local._evaluate_owned_family_local_math_v2(record, journal, observed_at=at)
    assert len(actual.gates) == 10 and all(item.passed for item in actual.gates), (
        actual.fail_codes
    )
    # One synthetic DATA-only G11 gate exists solely to exercise serialization.
    # It is never passed to native G12/reserve/SQL/submit or treated as risk truth.
    values = {name: getattr(actual, name) for name in type(actual).model_fields}
    values["gates"] = (
        *actual.gates,
        local.GateAssessment(
            report_id=actual.report_id,
            gate=local.QualificationGate.RISK,
            passed=True,
            code="passed",
            reason="Synthetic historical codec consistency only; no risk authority.",
            measured_values={"synthetic_data_only": True, "execution_authority": False},
        ),
    )
    data_only = local.OwnedFamilyEntryQualificationResultV2(**values)
    decoded = local.decode_owned_family_result_v2(
        data_only.model_dump(mode="json", round_trip=True)
    )
    assert decoded == data_only and decoded.execution_authority is False
    snapshot = evidence.prepare_native_evidence_v2(
        record["market"],
        record["analysis"],
        qualification=data_only,
        detection=record["computed"].detection,
        quote=record["quote_context"].quote,
        protection=selected,
        prepared_at=at,
        zone_tick_size=record["tick_size"],
    )
    assert type(snapshot) is evidence.NativeFamilyEvidenceSnapshotV3
    assert type(snapshot.qualification) is local.OwnedFamilyEntryQualificationResultV2
    assert (
        snapshot.execution_authority is False
        and snapshot.source_authenticity_verified is False
    )
    assert evidence.validate_native_snapshot_v2(snapshot) == snapshot
    replay = evidence.NativeFamilyEvidenceSnapshotV3.model_validate_json(
        snapshot.model_dump_json(round_trip=True), strict=True
    )
    assert replay == snapshot and type(replay.quote) is type(snapshot.quote)
    assert replay.quote.funding == snapshot.quote.funding
    assert evidence.validate_native_snapshot_v2(replay) == snapshot
    with pytest.raises(local.CurrentOriginalPrefixError):
        local.consume_owned_original_local_gates_v13(data_only)


@pytest.mark.parametrize(
    "strategy,direction",
    HISTORY_CASES,
    ids=["-".join(value) for value in HISTORY_CASES],
)
def test_original_g1_g5_scenarios_remain_genuinely_adverse(
    strategy, direction, tmp_path
):
    source = raw_fixture.history_raw_source(strategy, direction)
    capture_root = tmp_path / "original-adverse"
    capture_root.mkdir(exist_ok=False)
    diagnostic, packet, args, original = _capture_raw_family_values_v3(
        source,
        strategy,
        capture_root,
    )
    document = decode(original.receipt_json)
    if strategy == "liquidity_sweep_reversal":
        assert original.intent is None
        assert document["code"] == "original_sweep_chronology_rejected"
        event = document["history_admission"]["detection"]["trigger"]
        assert datetime.fromisoformat(document["created_at"]) >= datetime.fromisoformat(
            event["expires_at"]
        )
        return
    assert original.intent is not None, document["code"]
    values = _family_replay_values_v3(
        strategy, direction, diagnostic, packet, args, original
    )
    _record, _journal, _at, selected = _pure_local(values)
    assert selected.fail_codes == ("no_valid_structural_bracket",)
    assert selected.selected is None and not selected.protection_valid
    if strategy == "structure_reversal":
        assert any(
            "entry_inside_opposing_zone" in target.rejection_codes
            for target in selected.targets
        )
        assert any(
            "intervening_structural_target" in target.rejection_codes
            for target in selected.targets
        )
    elif strategy == "volatility_expansion":
        assert selected.stops and all(
            "stop_inside_atr_noise" in stop.rejection_codes for stop in selected.stops
        )
        assert selected.targets and all(
            "target_wrong_side_or_tick_collapsed" in target.rejection_codes
            for target in selected.targets
        )
    else:
        assert strategy == "range_reversal"
        nearest = Decimal("100.5") if direction == "long" else Decimal("99.5")
        assert any(
            target.final_target == nearest and not target.rejection_codes
            for target in selected.targets
        )
        assert any(
            "intervening_structural_target" in target.rejection_codes
            for target in selected.targets
        )
        assert any(
            "net_rr_below_minimum" in pair.rejection_codes
            for pair in selected.alternatives
        )


@pytest.mark.parametrize("direction", ("long", "short"))
def test_exact_R4_range_quote_outside_original_zone_remains_rejected(
    direction, tmp_path
):
    source = _owned_adverse_full_bracket_raw_source_v3("range_reversal", direction)
    capture = tmp_path / "exact-R4-adverse-range"
    capture.mkdir(exist_ok=False)
    _diagnostic, _packet, _args, original = _capture_raw_family_values_v3(
        source,
        "range_reversal",
        capture,
    )
    document = decode(original.receipt_json)
    assert original.intent is None
    assert document["code"] == "fixed_initial_entry_outside_original_zone"
    entry = Decimal(document["initial_entry"])
    if direction == "long":
        assert entry == Decimal("100.11")
        assert Decimal(document["zone"]["zone_high"]) == entry - Decimal("0.01")
    else:
        assert entry == Decimal("99.89")
        assert Decimal(document["zone"]["zone_low"]) == entry + Decimal("0.01")
    assert document["g1"]["passed"] is True
    assert original.execution_authority is False


@pytest.mark.parametrize("direction", ("long", "short"))
def test_exact_R4_sweep_non_neutral_history_remains_rejected(direction, tmp_path):
    source = _owned_adverse_full_bracket_raw_source_v3(
        "liquidity_sweep_reversal", direction
    )
    capture = tmp_path / "exact-R4-adverse-sweep"
    capture.mkdir(exist_ok=False)
    _diagnostic, _packet, _args, original = _capture_raw_family_values_v3(
        source,
        "liquidity_sweep_reversal",
        capture,
    )
    document = decode(original.receipt_json)
    assert original.intent is None
    assert document["code"] == "prior_range_htf_not_neutral"
    assert document["g1"]["passed"] is True
    assert original.execution_authority is False


@pytest.mark.parametrize(
    "peak,hours,direction",
    tuple(
        (peak, hours, direction)
        for peak in ("99.0", "99.5")
        for hours in (12, 16)
        for direction in ("long", "short")
    ),
)
def test_all_original_common_tape_trials_remain_rejected(
    peak, hours, direction, tmp_path
):
    # These are ALL eight retained fixed input configurations from the actual
    # audited diagnostic. No configuration disappears after the new scenario.
    source = _common_sweep_raw_source_v4(peak, hours, direction)
    capture = tmp_path / "original-adverse-common-tape"
    capture.mkdir(exist_ok=False)
    _diagnostic, _packet, _args, original = _capture_raw_family_values_v3(
        source,
        "liquidity_sweep_reversal",
        capture,
    )
    document = decode(original.receipt_json)
    assert original.intent is None
    assert document["code"] == "current_route_not_exact_range_transition"
    assert document["g1"]["passed"] is True
    assert original.execution_authority is False


@pytest.mark.parametrize("direction", ("long", "short"))
def test_exact_R7_common_sweep_high_volatility_stays_rejected(direction, tmp_path):
    # Preserve the full actual failed R7 source under its explicit old profile.
    # It must not be rescued or relabelled when a new source scenario is added.
    source = _common_sweep_raw_source_v4(
        "99.5", 16, direction, corrected=True, profile="R7"
    )
    capture = tmp_path / "exact-R7-adverse-common-sweep"
    capture.mkdir(exist_ok=False)
    _diagnostic, _packet, _args, original = _capture_raw_family_values_v3(
        source,
        "liquidity_sweep_reversal",
        capture,
    )
    value = decode(original.receipt_json)
    assert original.intent is None
    assert value["code"] == "prior_range_volatility_denied"
    assert (
        value["history_admission"]["range_context"]["views"]["4H"]["volatility"]
        == "high"
    )
    assert value["g1"]["passed"] is True
    assert original.execution_authority is False


@pytest.mark.parametrize("direction", ("long", "short"))
def test_exact_R8_true_true_momentum_pair_stays_rejected(direction, tmp_path):
    # Preserve the entire actual R8 failure, including its correctly neutral
    # normal-volatility HTF history. A pre-setup transition cannot be renewed.
    source = _common_sweep_raw_source_v4(
        "99.5",
        16,
        direction,
        corrected=True,
        profile="R8",
    )
    capture = tmp_path / "exact-R8-adverse-no-post-setup-transition"
    capture.mkdir(exist_ok=False)
    _diagnostic, _packet, _args, original = _capture_raw_family_values_v3(
        source,
        "liquidity_sweep_reversal",
        capture,
    )
    value = decode(original.receipt_json)
    assert original.intent is None
    assert value["code"] == "original_sweep_event_rejected"
    detection = value["history_admission"]["detection"]
    assert detection["trigger"] is None
    assert detection["fail_codes"] == ["trigger_missing"]
    assert ["missing_reason", "no_new_post_setup_momentum_transition"] in detection[
        "setup_basis"
    ]
    assert value["g1"]["passed"] is True
    assert original.execution_authority is False


@pytest.mark.parametrize("direction", ("long", "short"))
def test_exact_R9_valid_trigger_does_not_override_the_mathematical_veto(
    direction, tmp_path
):
    # This exact actual R9 source has a genuine unexpired post-setup event,
    # yet its opposing macro operands and insufficient core remain a veto.
    source = _common_sweep_raw_source_v4(
        "99.5",
        16,
        direction,
        corrected=True,
        profile="R9",
    )
    capture = tmp_path / "exact-R9-valid-event-mathematical-veto"
    capture.mkdir(exist_ok=False)
    _diagnostic, _packet, _args, original = _capture_raw_family_values_v3(
        source,
        "liquidity_sweep_reversal",
        capture,
    )
    value = decode(original.receipt_json)
    assert original.intent is None
    assert value["code"] == "current_mathematical_veto"
    history = value["history_admission"]
    detection = history["detection"]
    assert detection["fail_codes"] == []
    trigger = detection["trigger"]
    assert trigger["invalidation_reason"] is None
    assert trigger["trigger_time"] == "2026-09-12T10:10:00Z"
    assert trigger["expires_at"] == "2026-09-12T10:15:00Z"
    math = history["mathematical_confirmation"]
    assert math["status"] == "insufficient"
    assert Decimal(math["coverage"]) < Decimal("0.35")
    assert Decimal(math["directional_support"]) < 0
    assert value["g1"]["passed"] is True
    assert original.execution_authority is False
