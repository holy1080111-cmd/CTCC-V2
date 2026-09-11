"""Synthetic, source-bound static figures; no observed samples or order writes."""

import hashlib
import json
from datetime import timedelta
from decimal import ROUND_DOWN, Context, Decimal, Inexact, Rounded, localcontext
from io import BytesIO
from itertools import pairwise

import pytest
from PIL import Image, ImageChops, ImageStat

from app.trade_evidence import renderer
from app.trade_evidence.renderer import EvidenceRenderError, render_evidence
from app.trade_evidence.service import EvidenceError, prepare_evidence
from tests.unit.test_trade_evidence_models import evidence_inputs
from tests.unit.test_trade_evidence_pipeline import synthetic_snapshot

D = Decimal


@pytest.fixture(scope="module")
def packets():
    return {
        case: (snapshot := synthetic_snapshot(case), render_evidence(snapshot))
        for case in ("long", "short", "wait", "cancel")
    }


@pytest.mark.parametrize("case", ["long", "short", "wait", "cancel"])
def test_exact_six_immutable_bytes_preserve_complete_source_and_authority(
    packets, case
):
    snapshot, packet = packets[case]
    assert set(packet) == set(renderer.IMAGE_NAMES) | {"report.json"}
    assert all(type(value) is bytes for value in packet.values())
    with pytest.raises(TypeError):
        packet["4h.png"] = b"replaced"
    report = json.loads(packet["report.json"])
    assert packet["report.json"] == json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert report["schema_version"] == "ctcc.trade_evidence.v1"
    assert report["report_id"] == snapshot.report_id
    assert report["snapshot"] == snapshot.model_dump(mode="json", round_trip=True)
    assert report["snapshot"]["source_json"] == snapshot.source_json
    assert report["snapshot"]["protection_audit_json"] == snapshot.protection_audit_json
    for key in (
        "execution_authority",
        "gate_assessments_verified",
        "source_authenticity_verified",
    ):
        assert report[key] is report["snapshot"][key] is False
    assert report["snapshot"]["evidence_gate"] == "not_evaluated"
    assert report["snapshot"]["execution_recheck"] == "not_evaluated"
    assert not snapshot.qualification.qualified
    assert not snapshot.qualification.evidence_complete
    assert "completed_at" not in report and "sha256" not in report
    assert set(report["images"]) == set(renderer.IMAGE_NAMES)
    for name in renderer.IMAGE_NAMES:
        assert report["images"][name] == {
            "sha256": hashlib.sha256(packet[name]).hexdigest(),
            "size_bytes": len(packet[name]),
        }
    assert len(packet["report.json"]) <= renderer.MAX_REPORT_BYTES


@pytest.mark.parametrize("case", ["long", "short", "wait", "cancel"])
@pytest.mark.parametrize("name", renderer.IMAGE_NAMES)
def test_all_pngs_decode_have_fixed_dimensions_nonblank_and_embedded_identity(
    packets, case, name
):
    snapshot, packet = packets[case]
    with Image.open(BytesIO(packet[name])) as image:
        image.load()
        assert image.format == "PNG" and image.mode == "RGB"
        assert image.size == (
            renderer.SUMMARY_SIZE if name == "summary.png" else renderer.PANEL_SIZE
        )
        assert ImageChops.difference(
            image, Image.new("RGB", image.size, "white")
        ).getbbox()
        assert sum(ImageStat.Stat(image).stddev) > 20
        assert image.info["Software"] == renderer.RENDERER_VERSION
        assert image.info["Report"] == snapshot.report_id
        assert image.info["SourceSHA256"] == snapshot.source_sha256
        assert image.info["Purpose"] == "synthetic_test"
        assert image.info["Image"] == name
        assert image.info["Authority"] == "render_only_no_gate_or_execution_authority"
        assert not any("time" in key.lower() for key in image.info)
    assert 1000 < len(packet[name]) <= renderer.MAX_IMAGE_BYTES


def test_same_environment_repeats_all_bytes_under_hostile_decimal_context(packets):
    snapshot, packet = packets["long"]
    before = snapshot.model_dump_json(round_trip=True)
    with localcontext(Context(prec=6, rounding=ROUND_DOWN)) as context:
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        repeated = render_evidence(snapshot)
    assert repeated == packet
    assert snapshot.model_dump_json(round_trip=True) == before
    identity = json.loads(packet["report.json"])["renderer"]
    assert (
        identity["font_sha256"]
        == hashlib.sha256(renderer._font(21).font_bytes).hexdigest()
    )
    assert identity["pillow_version"] == "12.3.0"
    assert "cross-OS" in identity["reproducibility"]


@pytest.mark.parametrize(
    "fraction,expected", [("0", 600), ("0.25", 500), ("0.5", 400), ("1", 200)]
)
def test_sub_float_price_differences_preserve_pixel_geometry(fraction, expected):
    # All these values become the same binary float if converted before subtraction.
    with localcontext(Context(prec=100)):
        low = D("10000000000000000000")
        high = low + D("0.00000000000000000004")
        value = low + (high - low) * D(fraction)
    assert float(low) == float(high)
    assert renderer._price_pixel(value, low, high, 200, 600) == expected


def test_readable_absolute_axis_and_tiny_span_explicit_origin_fallback():
    ticks, offset = renderer._axis_ticks(D(90), D(110), D(95))
    assert not offset
    assert tuple(label for _, label in ticks) == ("90", "94", "98", "102", "106", "110")
    with localcontext(Context(prec=100)):
        low = D("10000000000000000000")
        high = low + D("4e-20")
    ticks, offset = renderer._axis_ticks(low, high, low)
    assert offset
    assert len({label for _, label in ticks}) == 6
    assert ticks[0][1] == "0"
    assert D(ticks[-1][1]) == D("4e-20")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2.363636363636363636", "2.36364"),
        ("100.5000000000000000", "100.5"),
        ("0.00000000000000000001", "1e-20"),
    ],
)
def test_compact_number_does_not_turn_ordinary_rr_into_scientific_notation(
    value, expected
):
    assert renderer._number(D(value), compact=True).lower() == expected


@pytest.mark.parametrize(
    "actual", [(), (300,), (248, 249, 250, 595, 596), (601,) * 11, (248,) * 11]
)
def test_label_rail_is_near_source_nonoverlapping_and_bounded(actual):
    positioned = renderer._label_positions(actual, 248, 601)
    assert len(positioned) == len(actual)
    assert all(248 <= value <= 601 for value in positioned)
    assert all(b - a >= 24 for a, b in pairwise(positioned))
    if len(actual) == 1:
        assert positioned == actual


def test_labels_do_not_move_uncrowded_entry_and_stop_to_top():
    assert renderer._label_positions((265, 430, 555), 248, 601) == (265, 430, 555)


def test_actual_open_close_utc_time_coordinates(packets):
    snapshot, _ = packets["long"]
    for panel in snapshot.panels:
        first, last = panel.candles[0], panel.candles[-1]
        assert (
            renderer._time_pixel(
                first.open_time, panel.crop_start, panel.crop_end, 160, 1395
            )
            == 160
        )
        assert (
            renderer._time_pixel(
                last.close_time, panel.crop_start, panel.crop_end, 160, 1395
            )
            == 1395
        )
        assert (
            renderer._time_pixel(
                first.close_time, panel.crop_start, panel.crop_end, 160, 1395
            )
            > 160
        )


def test_ascii_escaping_is_explicit_instead_of_missing_glyphs_or_control_chars():
    value = "繁體中文\n€🙂\t"
    label = renderer._ascii(value)
    assert label == r"\u7e41\u9ad4\u4e2d\u6587\n\u20ac\U0001f642\t"
    assert all(32 <= ord(character) <= 126 for character in label)
    assert renderer._ascii(None) == "unknown"


def test_original_unicode_survives_json_and_visible_overflow_is_declared():
    market, analysis, arguments = evidence_inputs(prefix=7)
    q = arguments["qualification"]
    last = q.gates[-1].model_copy(
        update={
            "passed": False,
            "code": "failure_" + "x" * 88,
            "reason": "尚未完成來源確認；這是合成測試。",
        }
    )
    arguments["qualification"] = q.model_copy(update={"gates": (*q.gates[:-1], last)})
    snapshot = prepare_evidence(market, analysis, **arguments)
    packet = render_evidence(snapshot)
    assert last.reason.encode("utf-8") in packet["report.json"]
    report = json.loads(packet["report.json"])
    assert report["snapshot"]["qualification"]["gates"][-1]["reason"] == last.reason
    assert "gate_G7" in report["display_overflow_fields"]["summary.png"]
    assert len(report["snapshot"]["qualification"]["gates"][-1]["code"]) == 96


@pytest.mark.parametrize("seconds,expired", [(-1, False), (0, True), (1, True)])
def test_preparation_expiry_warning_never_changes_recorded_gates(seconds, expired):
    market, analysis, arguments = evidence_inputs(prefix=11)
    q = arguments["qualification"]
    before = q.model_dump_json(round_trip=True)
    arguments["prepared_at"] = q.trigger.expires_at + timedelta(seconds=seconds)
    snapshot = prepare_evidence(market, analysis, **arguments)
    label = renderer._preparation_timing(snapshot)
    assert ("EXPIRED AT PREPARATION" in label) is expired
    assert "recheck" in label
    assert snapshot.qualification.model_dump_json(round_trip=True) == before
    assert snapshot.qualification.evaluated_at < snapshot.prepared_at


def test_summary_explicitly_labels_evaluation_time_and_missing_later_gates(
    packets, monkeypatch
):
    seen = []
    original = renderer._Canvas.text

    def capture(canvas, xy, value, **kwargs):
        seen.append(str(value))
        return original(canvas, xy, value, **kwargs)

    monkeypatch.setattr(renderer._Canvas, "text", capture)
    snapshot, _ = packets["long"]
    renderer._summary(snapshot)
    text = "\n".join(seen)
    assert "GATES - recorded claims" in text
    assert renderer._at(snapshot.qualification.evaluated_at) in text
    assert "EVIDENCE: NOT EVALUATED" in text
    assert "RECHECK: NOT EVALUATED" in text
    assert "ORDER ELIGIBLE: NO" in text
    assert "not current" in text
    assert "PASS CLAIM" in text and "ALL PASS" not in text


def test_wait_snapshot_keeps_unknown_prices_and_indicator_warmup(packets, monkeypatch):
    snapshot, packet = packets["wait"]
    assert snapshot.qualification.candidate_entry is None
    assert snapshot.quote is None and snapshot.qualification.trigger is None
    assert "unavailable" in renderer._preparation_timing(snapshot)
    panel = snapshot.panels[-1]
    assert all(row.ema50 is row.ema200 is None for row in panel.candles)
    assert any(row.ema20 is None for row in panel.candles)
    assert any(row.ema20 is not None for row in panel.candles)
    assert renderer._critical(snapshot) == []
    assert b'"candidate_entry":null' in packet["report.json"]
    seen = []
    original = renderer._Canvas.text

    def capture(canvas, xy, value, **kwargs):
        seen.append(str(value))
        return original(canvas, xy, value, **kwargs)

    monkeypatch.setattr(renderer._Canvas, "text", capture)
    renderer._panel(snapshot, panel)
    assert any("Entry unknown   SL unknown   TP unknown" in value for value in seen)
    assert any("independent scale" in value for value in seen)
    assert any("do not prove historical active states" in value for value in seen)


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_authority", True),
        ("source_authenticity_verified", True),
        ("evidence_gate", "passed"),
        ("source_sha256", "a" * 64),
        ("candidate_sha256", "b" * 64),
    ],
)
def test_render_revalidates_tampered_snapshot_before_any_png(
    packets, monkeypatch, field, value
):
    snapshot, _ = packets["long"]
    monkeypatch.setattr(
        renderer, "_panel", lambda *args: pytest.fail("must reject before PNG")
    )
    with pytest.raises(EvidenceError):
        render_evidence(snapshot.model_copy(update={field: value}))


def test_render_rebuilds_source_ema_and_rejects_forged_chart_data(packets, monkeypatch):
    snapshot, _ = packets["long"]
    panel = snapshot.panels[0]
    forged = panel.candles[0].model_copy(update={"ema20": D(999)})
    panel = panel.model_copy(update={"candles": (forged, *panel.candles[1:])})
    snapshot = snapshot.model_copy(update={"panels": (panel, *snapshot.panels[1:])})
    monkeypatch.setattr(
        renderer, "_panel", lambda *args: pytest.fail("must reject before PNG")
    )
    with pytest.raises(EvidenceError):
        render_evidence(snapshot)


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("__version__", "0.0.0", "pinned_pillow"),
        ("FONT_SHA256", "f" * 64, "pinned_embedded_font"),
    ],
)
def test_missing_pinned_renderer_dependency_fails_closed(
    packets, monkeypatch, field, value, reason
):
    monkeypatch.setattr(renderer, field, value)
    with pytest.raises(EvidenceRenderError, match=reason):
        render_evidence(packets["long"][0])


def test_report_bound_is_checked_before_any_expensive_png(packets, monkeypatch):
    monkeypatch.setattr(renderer, "MAX_REPORT_BYTES", 1000)
    monkeypatch.setattr(
        renderer, "_panel", lambda *args: pytest.fail("must reject before PNG")
    )
    with pytest.raises(EvidenceRenderError, match="report_byte_limit_exceeded"):
        render_evidence(packets["long"][0])


def test_report_bound_is_rechecked_after_display_overflow_audit(packets, monkeypatch):
    snapshot, packet = packets["long"]
    monkeypatch.setattr(renderer, "MAX_REPORT_BYTES", len(packet["report.json"]) + 1000)
    original = renderer._summary

    def excessive_notes(snapshot):
        canvas = original(snapshot)
        canvas.truncated.extend(["x" * 10000])
        return canvas

    monkeypatch.setattr(renderer, "_summary", excessive_notes)
    with pytest.raises(EvidenceRenderError, match="report_byte_limit_exceeded"):
        render_evidence(snapshot)


def test_image_bound_prevents_any_successful_packet(packets, monkeypatch):
    monkeypatch.setattr(renderer, "MAX_IMAGE_BYTES", 1000)
    with pytest.raises(EvidenceRenderError, match="image_byte_limit_exceeded"):
        render_evidence(packets["long"][0])
