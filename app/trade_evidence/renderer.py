"""Deterministic, bytes-only static evidence rendering, not gate authority.

Pillow's bundled Aileron font is pinned; no host font lookup, network, file write,
clock, or runtime order state is used. PNG bytes are reproducible in the same
Pillow/FreeType/zlib environment, not promised identical across operating systems.
Original Unicode remains in report.json; PNG labels explicitly use ASCII escapes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext
from io import BytesIO
from types import MappingProxyType

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin, __version__, features

from app.trade_evidence.models import EvidenceSnapshot
from app.trade_evidence.service import validate_snapshot

D = Decimal
RENDERER_VERSION = "ctcc-pillow-evidence-v1"
PILLOW_VERSION = "12.3.0"
FONT_SHA256 = "69853909b940023570964e29cffe30da95aea8de3627736b5cd15ab30143169f"
IMAGE_NAMES = ("4h.png", "1h.png", "15m.png", "5m.png", "summary.png")
PANEL_SIZE = (1600, 1000)
SUMMARY_SIZE = (1600, 1100)
MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
_INK = "#1c2d40"
_MUTED = "#536478"
_GRID = "#dce3eb"
_BLUE = "#2467a6"
_GOLD = "#a06b14"
_ORANGE = "#b8542c"
_PALE_BLUE = "#e7f0f8"
_PALE_GOLD = "#faf1df"
_WHITE = "#ffffff"
_FOOTNOTE = "ASCII-safe labels; non-ASCII is escaped. Full original text and exact values: report.json."


class EvidenceRenderError(ValueError):
    pass


def _json_bytes(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _ascii(value) -> str:
    if value is None:
        return "unknown"
    return "".join(
        character
        if 32 <= ord(character) <= 126
        else character.encode("unicode_escape").decode("ascii")
        for character in str(value)
    )


def _number(value: Decimal | None, *, compact=False) -> str:
    if value is None:
        return "unknown"
    if value == 0:
        return "0"
    exact = format(value, "f")
    if "." in exact:
        exact = exact.rstrip("0").rstrip(".")
    if not compact or len(exact) <= 13:
        return exact
    # Six significant figures keep ordinary RR/prices readable. Scientific
    # notation is reserved for scales where a short fixed label cannot fit.
    rounded = format(value, ".6g")
    if "e" not in rounded.lower() and "." in rounded:
        rounded = rounded.rstrip("0").rstrip(".")
    return rounded


def _at(value: datetime | None) -> str:
    return (
        "unknown"
        if value is None
        else value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    )


def _font(size: int):
    if __version__ != PILLOW_VERSION:
        raise EvidenceRenderError("pinned_pillow_version_required")
    font = ImageFont.load_default(size=size)
    if (
        not isinstance(font, ImageFont.FreeTypeFont)
        or hashlib.sha256(font.font_bytes).hexdigest() != FONT_SHA256
    ):
        raise EvidenceRenderError("pinned_embedded_font_required")
    return font


def _renderer_identity():
    return {
        "renderer_version": RENDERER_VERSION,
        "pillow_version": __version__,
        "font": "Pillow embedded Aileron Regular",
        "font_sha256": FONT_SHA256,
        "freetype_version": features.version_module("freetype2"),
        "zlib_version": features.version_codec("zlib"),
        "text_policy": "ASCII escapes; overflow marked [see JSON]; originals retained",
        "price_axis": "independent absolute ticks; origin plus delta only when labels alias",
        "reproducibility": "same renderer environment; cross-OS PNG bytes not guaranteed",
    }


class _Canvas:
    def __init__(self, size):
        self.image = Image.new("RGB", size, _WHITE)
        self.draw = ImageDraw.Draw(self.image)
        self.fonts = {size: _font(size) for size in (17, 19, 21, 23, 28, 36)}
        self.truncated = []

    def text(
        self, xy, value, *, size=21, fill=_INK, width=None, key="label", anchor=None
    ):
        text = _ascii(value)
        font = self.fonts[size]
        if width is not None and self.draw.textlength(text, font=font) > width:
            suffix = " [see JSON]"
            while text and self.draw.textlength(text + suffix, font=font) > width:
                text = text[:-1]
            text += suffix
            self.truncated.append(key)
        self.draw.text(xy, text, font=font, fill=fill, anchor=anchor)

    def paragraph(self, xy, value, *, width, lines, size=21, fill=_INK, key="text"):
        text = _ascii(value)
        font = self.fonts[size]
        words = text.split(" ")
        wrapped = []
        current = ""
        for word in words:
            trial = word if not current else current + " " + word
            if self.draw.textlength(trial, font=font) <= width:
                current = trial
            else:
                if current:
                    wrapped.append(current)
                current = word
        if current or not wrapped:
            wrapped.append(current)
        overflow = len(wrapped) > lines
        wrapped = wrapped[:lines]
        if overflow:
            wrapped[-1] += " [see JSON]"
            self.truncated.append(key)
        for index, line in enumerate(wrapped):
            self.text(
                (xy[0], xy[1] + index * (size + 7)),
                line,
                size=size,
                fill=fill,
                width=width,
                key=key,
            )
        return len(wrapped) * (size + 7)


def _line(draw, points, *, fill, width=1, dash=None):
    if dash is None:
        draw.line(points, fill=fill, width=width)
        return
    (x1, y1), (x2, y2) = points
    dx, dy = x2 - x1, y2 - y1
    distance = max(abs(dx), abs(dy))
    if distance == 0:
        return
    for start in range(0, int(distance) + 1, dash[0] + dash[1]):
        end = min(start + dash[0], distance)
        draw.line(
            (
                (x1 + dx * start / distance, y1 + dy * start / distance),
                (x1 + dx * end / distance, y1 + dy * end / distance),
            ),
            fill=fill,
            width=width,
        )


def _price_pixel(
    value: Decimal, low: Decimal, high: Decimal, top: int, bottom: int
) -> int:
    # Never float a large absolute price before subtracting its exact origin.
    with localcontext(Context(prec=100)):
        ratio = (value - low) / (high - low)
        return bottom - round(float(ratio) * (bottom - top))


def _time_pixel(value: datetime, start: datetime, end: datetime, left: int, right: int):
    interval, elapsed = end - start, value - start
    total = (interval.days * 86400 + interval.seconds) * 1000000 + interval.microseconds
    offset = (elapsed.days * 86400 + elapsed.seconds) * 1000000 + elapsed.microseconds
    return left + round(offset / total * (right - left))


def _axis_ticks(low, high, origin):
    with localcontext(Context(prec=100)):
        values = tuple(low + (high - low) * D(index) / D(5) for index in range(6))
        absolute = tuple(_number(value, compact=True) for value in values)
        offset = len(set(absolute)) != len(values)
        labels = (
            tuple(_number(value - origin, compact=True) for value in values)
            if offset
            else absolute
        )
        return tuple(zip(values, labels, strict=True)), offset


def _label_positions(actual, top, bottom):
    """Nearest possible monotone labels, bounded by two collision passes."""
    if not actual:
        return ()
    gap = min(24, (bottom - top) // max(1, len(actual) - 1))
    positioned = []
    for yy in actual:
        positioned.append(max(top, yy, positioned[-1] + gap if positioned else top))
    positioned[-1] = min(bottom, positioned[-1])
    for index in range(len(positioned) - 2, -1, -1):
        positioned[index] = min(positioned[index], positioned[index + 1] - gap)
    return tuple(positioned)


def _critical(snapshot):
    qualification = snapshot.qualification
    values = []
    for label, value, color, dash in (
        ("ENTRY", qualification.candidate_entry, _INK, None),
        ("SL", qualification.stop_loss, _ORANGE, (10, 5)),
        ("TP", qualification.take_profit, _BLUE, (3, 4)),
    ):
        if value is not None:
            values.append((label, value, color, dash))
    if snapshot.quote is not None:
        values.append(
            (
                "QUOTE",
                snapshot.quote.ask
                if snapshot.direction == "long"
                else snapshot.quote.bid,
                _MUTED,
                (2, 5),
            )
        )
    return values


def _preparation_timing(snapshot):
    """Describe a clock fact without renewing or changing recorded gate claims."""
    trigger = snapshot.qualification.trigger
    if trigger is None:
        return "Trigger unavailable; current eligibility unknown; recheck required."
    if snapshot.prepared_at >= trigger.expires_at:
        return "EXPIRED AT PREPARATION; recheck required; recorded gates unchanged."
    return (
        "Before recorded expiry at preparation; current eligibility requires recheck."
    )


def _panel(snapshot, panel):
    canvas = _Canvas(PANEL_SIZE)
    draw = canvas.draw
    q = snapshot.qualification
    canvas.text(
        (48, 30), f"CTCC  |  {panel.timeframe} source evidence", size=36, width=1090
    )
    canvas.text((1550, 37), snapshot.purpose.upper(), size=21, fill=_GOLD, anchor="ra")
    canvas.text(
        (48, 82),
        f"{snapshot.instrument_id}  |  {snapshot.strategy}  |  {snapshot.direction}",
        size=23,
        width=1504,
        key="instrument_strategy",
    )
    canvas.text(
        (48, 118),
        f"Regime: {q.market_regime.value}   Trend: {panel.trend}   Structure: {panel.structure}",
        width=1504,
        key="regime_structure",
    )
    canvas.text(
        (48, 150),
        f"Scores {q.raw_score}/{q.effective_score} (not probability) | Evaluated {_at(q.evaluated_at)} | Prepared {_at(snapshot.prepared_at)}",
        size=19,
        width=1504,
    )

    rows = panel.candles
    critical = _critical(snapshot)
    values = [
        value for row in rows for value in (row.open, row.high, row.low, row.close)
    ]
    values += [
        value
        for row in rows
        for value in (row.ema20, row.ema50, row.ema200)
        if value is not None
    ]
    values += [value for level in panel.levels for value in (level.low, level.high)]
    values += [item[1] for item in critical]
    zone = q.entry_zone
    if zone is not None:
        values.extend((zone.zone_low, zone.zone_high))
    trigger = q.trigger
    trigger_visible = (
        trigger is not None
        and panel.crop_start <= trigger.trigger_time <= panel.crop_end
    )
    if trigger_visible:
        values.append(trigger.trigger_price)
    with localcontext(Context(prec=100)):
        origin, ceiling = min(values), max(values)
        span = ceiling - origin
        if span == 0:
            span = origin / D(100)
        padding = span * D("0.07")
        low, high = origin - padding, ceiling + padding
        if low <= 0:
            low = D(0)
        if high <= low:
            raise EvidenceRenderError("price_axis_has_no_range")
    left, right, top, bottom = 160, 1395, 239, 610
    y = lambda value: _price_pixel(value, low, high, top, bottom)
    x = lambda value: _time_pixel(value, panel.crop_start, panel.crop_end, left, right)
    ticks, offset_axis = _axis_ticks(low, high, origin)
    canvas.text(
        (48, 194),
        (
            f"Price = origin {_number(origin)} + axis delta  |  independent scale; not zero-based"
            if offset_axis
            else "Price (absolute)  |  independent scale for this timeframe; not zero-based"
        ),
        size=19,
        width=1504,
        key="axis_origin",
    )
    for value, label in ticks:
        yy = y(value)
        draw.line(((left, yy), (right, yy)), fill=_GRID, width=1)
        canvas.text(
            (left - 12, yy),
            label,
            size=17,
            fill=_MUTED,
            anchor="rm",
        )
    draw.line(((left, top), (left, bottom), (right, bottom)), fill=_MUTED, width=1)

    # Current surviving-level references start at known-at, not before discovery.
    # The selected inventory does NOT prove past continuous active-state history.
    level_labels = []
    kind_counts = {}
    for level in panel.levels:
        kind = str(level.kind)
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        if level.known_at > panel.crop_end or level.as_of < panel.crop_start:
            continue
        start_x = x(max(panel.crop_start, level.known_at))
        end_x = x(min(panel.crop_end, level.as_of))
        if start_x > end_x:
            continue
        color = _BLUE if "support" in kind or "low" in kind else _GOLD
        if level.low != level.high:
            draw.rectangle(
                (start_x, y(level.high), end_x, y(level.low)),
                fill=_PALE_BLUE if "fvg" in kind else _PALE_GOLD,
            )
        _line(
            draw,
            ((start_x, y(level.low)), (end_x, y(level.low))),
            fill=color,
            dash=(4, 6),
        )
        if level.high != level.low:
            _line(
                draw,
                ((start_x, y(level.high)), (end_x, y(level.high))),
                fill=color,
                dash=(4, 6),
            )
        # One direct label per source kind; all exact lines and source records
        # remain plotted/retained, without allowing a crowded label cloud.
        if kind_counts[kind] == 1:
            level_labels.append((kind.upper(), level.low, color))
    if (
        zone is not None
        and zone.created_at <= panel.crop_end
        and zone.expires_at >= panel.crop_start
    ):
        zx1, zx2 = (
            x(max(panel.crop_start, zone.created_at)),
            x(min(panel.crop_end, zone.expires_at)),
        )
        _line(
            draw,
            ((zx1, y(zone.zone_low)), (zx2, y(zone.zone_low))),
            fill=_INK,
            width=2,
            dash=(12, 4),
        )
        _line(
            draw,
            ((zx1, y(zone.zone_high)), (zx2, y(zone.zone_high))),
            fill=_INK,
            width=2,
            dash=(12, 4),
        )
        level_labels.append(("IDEAL ZONE", zone.zone_high, _INK))

    # Candles retain their actual opening/closing interval, not a guessed cadence.
    for row in rows:
        x1, x2 = x(row.open_time), x(row.close_time)
        mid, half = (x1 + x2) // 2, max(1, round((x2 - x1) * 0.28))
        color = _BLUE if row.close >= row.open else _GOLD
        draw.line(((mid, y(row.high)), (mid, y(row.low))), fill=color, width=2)
        body_top, body_bottom = sorted((y(row.open), y(row.close)))
        draw.rectangle(
            (mid - half, body_top, mid + half, max(body_top + 1, body_bottom)),
            fill=_WHITE if row.close >= row.open else _GOLD,
            outline=color,
            width=2,
        )
    for field, color, dash in (
        ("ema20", _BLUE, None),
        ("ema50", _GOLD, (8, 4)),
        ("ema200", _MUTED, (2, 5)),
    ):
        prior = None
        for row in rows:
            value = getattr(row, field)
            point = None if value is None else (x(row.close_time), y(value))
            if point is not None and prior is not None:
                _line(draw, (prior, point), fill=color, width=2, dash=dash)
            prior = point
    for label, value, color, dash in critical:
        _line(
            draw, ((left, y(value)), (right, y(value))), fill=color, width=2, dash=dash
        )
        level_labels.append((label, value, color))
    if trigger_visible:
        tx, ty = x(trigger.trigger_time), y(trigger.trigger_price)
        _line(draw, ((tx, top), (tx, bottom)), fill=_INK, dash=(3, 5))
        draw.ellipse(
            (tx - 6, ty - 6, tx + 6, ty + 6), fill=_WHITE, outline=_INK, width=3
        )
        level_labels.append(("TRIGGER", trigger.trigger_price, _INK))
    # A bounded direct-label rail, with leaders tied to exact price positions.
    label_rows = sorted(level_labels, key=lambda item: (-item[1], item[0]))
    positions = _label_positions([y(row[1]) for row in label_rows], top + 9, bottom - 9)
    for (label, value, color), yy in zip(label_rows, positions, strict=True):
        draw.line(
            ((right, y(value)), (right + 12, y(value)), (right + 20, yy)),
            fill=color,
            width=1,
        )
        canvas.text(
            (right + 25, yy - 9),
            label
            + " "
            + (
                _number(value - origin, compact=True) + "d"
                if offset_axis
                else _number(value, compact=True)
            ),
            size=17,
            fill=color,
            width=155,
            key="level_label",
        )

    indices = sorted({0, len(rows) // 3, 2 * len(rows) // 3})
    for index in indices:
        instant = rows[index].open_time
        canvas.text(
            (x(instant), bottom + 19),
            instant.strftime("%m-%d %H:%M"),
            size=17,
            fill=_MUTED,
            anchor="ma",
        )
    canvas.text(
        (right, bottom + 19),
        rows[-1].close_time.strftime("%m-%d %H:%M"),
        size=17,
        fill=_MUTED,
        anchor="ma",
    )
    canvas.text(
        (left, bottom + 48),
        "UTC candle intervals: open -> close; UP hollow / DOWN filled",
        size=19,
        fill=_MUTED,
        width=1300,
    )
    canvas.text((48, 705), "TRADE / EVENT", size=23)
    canvas.text((825, 705), "SOURCE / STRUCTURE", size=23)
    trade_lines = [
        f"Entry {_number(q.candidate_entry)}   SL {_number(q.stop_loss)}   TP {_number(q.take_profit)}",
        f"Ideal zone: {_number(zone.zone_low) + ' .. ' + _number(zone.zone_high) if zone else 'unknown'}",
        f"Trigger: {_at(trigger.trigger_time) if trigger else 'unknown'}; {'in crop' if trigger_visible else 'outside crop / unavailable'}",
        f"Captured quote: {_number(critical[-1][1]) if snapshot.quote else 'unknown'}  at {_at(snapshot.quote.quote_time) if snapshot.quote else 'unknown'}",
        f"Gross / net RR: {_number(q.gross_rr, compact=True)} / {_number(q.net_rr, compact=True)} (scenario)",
    ]
    source_lines = [
        f"Shown {len(rows)} / {panel.total_confirmed_candles} confirmed candles; crop is explicit",
        f"{_at(panel.crop_start)} -> {_at(panel.crop_end)}",
        "Levels: "
        + (
            ", ".join(f"{kind}={count}" for kind, count in sorted(kind_counts.items()))
            or "unknown; no observed source"
        ),
        "EMA 20 solid blue / 50 dashed gold / 200 dotted grey; gaps mean unavailable",
        "Surviving levels as of crop end; lines do not prove historical active states",
    ]
    for index, line in enumerate(trade_lines):
        canvas.text(
            (48, 748 + index * 29), line, size=19, width=740, key=f"trade_{index}"
        )
    for index, line in enumerate(source_lines):
        canvas.text(
            (825, 748 + index * 29), line, size=19, width=725, key=f"source_{index}"
        )
    canvas.text(
        (48, 920),
        f"Source SHA256 {snapshot.source_sha256}",
        size=17,
        fill=_MUTED,
        width=1504,
    )
    canvas.text(
        (48, 947),
        "RENDER ONLY: source authenticity and gate claims unverified; G12 / recheck / order authority NOT granted.",
        size=17,
        fill=_ORANGE,
        width=1504,
    )
    canvas.text((48, 974), _FOOTNOTE, size=17, fill=_MUTED, width=1504)
    return canvas


def _summary(snapshot):
    canvas = _Canvas(SUMMARY_SIZE)
    q = snapshot.qualification
    canvas.text((48, 30), "CTCC  |  Decision evidence summary", size=36, width=1250)
    canvas.text(
        (48, 83),
        f"{snapshot.instrument_id}  |  {snapshot.strategy}  |  {snapshot.direction}",
        size=23,
        width=1504,
    )
    canvas.text(
        (48, 124),
        f"{snapshot.purpose.upper()}  |  Report {snapshot.report_id}  |  {_at(snapshot.prepared_at)}",
        size=21,
        width=1504,
    )
    canvas.text(
        (48, 164),
        "PRE-EXECUTION EVIDENCE - not a trade recommendation or execution authorization",
        size=21,
        fill=_ORANGE,
        width=1504,
    )
    detection = snapshot.detection
    zone, trigger = q.entry_zone, q.trigger
    why = (
        f"Strategy {snapshot.strategy}; regime {q.market_regime.value}; HTF {q.htf_bias or 'unknown'}. "
        f"Setup state {q.setup_state}. Raw/effective score {q.raw_score}/{q.effective_score}; score is not probability."
    )
    why_now = (
        f"Recorded timing {q.entry_timing_state} at {_at(q.evaluated_at)}; "
        f"trigger {_at(trigger.trigger_time) if trigger else 'unknown'}; "
        f"expiry {_at(trigger.expires_at) if trigger else 'unknown'}."
    )
    entry = (
        f"Unchanged candidate {_number(q.candidate_entry)}; reference {_number(q.reference_price)}. "
        f"Ideal zone {_number(zone.zone_low) + ' .. ' + _number(zone.zone_high) if zone else 'unknown'}; "
        f"quote source {snapshot.quote.source if snapshot.quote else 'unknown'} at {_at(snapshot.quote.quote_time) if snapshot.quote else 'unknown'}."
    )
    invalidation = (
        f"Thesis {_number(detection.invalidation_price) if detection else 'unknown'}; "
        f"structural SL {_number(q.stop_loss)}. Protection alternatives: "
        f"{len(json.loads(snapshot.protection_audit_json).get('alternatives', [])) if snapshot.protection_audit_json else 'unknown'}. "
        "No break-even/trailing-stop authority."
    )
    target = (
        f"Structural TP {_number(q.take_profit)}; gross RR {_number(q.gross_rr, compact=True)}; "
        f"net RR {_number(q.net_rr, compact=True)} (explicit scenario, not expected value)."
    )
    for title, text, yy in (
        ("WHY", why, 228),
        ("WHY NOW - recorded, not current", why_now, 365),
        ("ENTRY", entry, 502),
        ("INVALIDATION", invalidation, 639),
        ("TARGET", target, 776),
    ):
        canvas.text((48, yy), title, size=23, fill=_BLUE)
        canvas.paragraph((48, yy + 35), text, width=935, lines=3, size=21, key=title)
    canvas.text((1050, 228), "GATES - recorded claims", size=23, fill=_BLUE)
    canvas.text(
        (1050, 266),
        f"At {_at(q.evaluated_at)}; unverified",
        size=17,
        fill=_MUTED,
        width=500,
    )
    names = (
        ("G1", "DATA"),
        ("G2", "REGIME"),
        ("G3", "HTF"),
        ("G4", "SETUP"),
        ("G5", "TRIGGER"),
        ("G6", "TIMING"),
        ("G7", "LOCATION"),
        ("G8", "STOP"),
        ("G9", "TARGET"),
        ("G10", "ECONOMICS"),
        ("G11", "RISK"),
        ("G12", "EVIDENCE"),
        ("execution_recheck", "RECHECK"),
    )
    gates = {item.gate.value: item for item in q.gates}
    for index, (key, label) in enumerate(names):
        gate = gates.get(key)
        status = (
            "NOT EVALUATED"
            if gate is None
            else "PASS CLAIM"
            if gate.passed
            else "FAIL: " + gate.code
        )
        if key in {"G12", "execution_recheck"}:
            status = "NOT EVALUATED"
        canvas.text(
            (1050, 310 + index * 40),
            f"{label}: {status}",
            size=19,
            fill=_ORANGE if gate is not None and not gate.passed else _INK,
            width=500,
            key=f"gate_{key}",
        )
    canvas.text((1050, 835), "ORDER ELIGIBLE: NO", size=23, fill=_ORANGE)
    canvas.paragraph(
        (1050, 872),
        _preparation_timing(snapshot),
        width=500,
        lines=2,
        size=17,
        fill=_ORANGE,
        key="preparation_timing",
    )
    canvas.text(
        (48, 943),
        f"Source SHA256 {snapshot.source_sha256}",
        size=17,
        fill=_MUTED,
        width=1504,
    )
    canvas.text(
        (48, 974),
        f"Candidate SHA256 {snapshot.candidate_sha256}",
        size=17,
        fill=_MUTED,
        width=1504,
    )
    canvas.text(
        (48, 1007),
        "Coverage: four independent timeframe crops. Source authenticity / gate assessments remain unverified.",
        size=17,
        fill=_ORANGE,
        width=1504,
    )
    canvas.text((48, 1040), _FOOTNOTE, size=17, fill=_MUTED, width=1504)
    return canvas


def _png(canvas, snapshot, filename):
    buffer = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    for key, value in (
        ("Software", RENDERER_VERSION),
        ("Report", snapshot.report_id),
        ("SourceSHA256", snapshot.source_sha256),
        ("Purpose", snapshot.purpose),
        ("Image", filename),
        ("Authority", "render_only_no_gate_or_execution_authority"),
    ):
        metadata.add_text(key, value)
    canvas.image.save(
        buffer, format="PNG", pnginfo=metadata, optimize=False, compress_level=9
    )
    payload = buffer.getvalue()
    if not 0 < len(payload) <= MAX_IMAGE_BYTES:
        raise EvidenceRenderError("image_byte_limit_exceeded")
    return payload


def _render_evidence(snapshot: EvidenceSnapshot) -> Mapping[str, bytes]:
    """Revalidate copied source evidence, then return exactly five PNGs and JSON.

    The caller publishes atomically and separately verifies G12/recheck. This
    function never updates a gate or adds a completion timestamp to the report.
    """
    checked = validate_snapshot(snapshot)
    report = {
        "schema_version": "ctcc.trade_evidence.v1",
        "report_id": checked.report_id,
        "snapshot": checked.model_dump(mode="json", round_trip=True),
        "renderer": _renderer_identity(),
        "images": {
            name: {"sha256": "0" * 64, "size_bytes": 99999999} for name in IMAGE_NAMES
        },
        "execution_authority": False,
        "gate_assessments_verified": False,
        "source_authenticity_verified": False,
    }
    if len(_json_bytes(report)) > MAX_REPORT_BYTES:
        raise EvidenceRenderError("report_byte_limit_exceeded")
    output = {}
    notes = {}
    for panel, name in zip(checked.panels, IMAGE_NAMES[:4], strict=True):
        canvas = _panel(checked, panel)
        output[name] = _png(canvas, checked, name)
        notes[name] = sorted(set(canvas.truncated))
    canvas = _summary(checked)
    output["summary.png"] = _png(canvas, checked, "summary.png")
    notes["summary.png"] = sorted(set(canvas.truncated))
    report["images"] = {
        name: {
            "sha256": hashlib.sha256(output[name]).hexdigest(),
            "size_bytes": len(output[name]),
        }
        for name in IMAGE_NAMES
    }
    report["display_overflow_fields"] = notes
    raw = _json_bytes(report)
    if len(raw) > MAX_REPORT_BYTES:
        raise EvidenceRenderError("report_byte_limit_exceeded")
    output["report.json"] = raw
    return MappingProxyType(output)


def render_evidence(snapshot: EvidenceSnapshot) -> Mapping[str, bytes]:
    """Render exactly six immutable byte artifacts, without publishing or gates."""
    with localcontext(Context(prec=100)):
        return _render_evidence(snapshot)
