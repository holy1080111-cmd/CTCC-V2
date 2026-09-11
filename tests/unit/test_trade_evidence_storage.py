"""Synthetic publication safety checks; no external images or account data."""

import hashlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from threading import Barrier
from types import MappingProxyType

import pytest
from PIL import Image
from pydantic import ValidationError

from app.trade_evidence import storage as storage_module
from app.trade_evidence.storage import EvidencePublicationError, publish_evidence

REPORT = "synthetic-evidence-report"
NOW = datetime(2026, 1, 12, 12, tzinfo=UTC)
IMAGES = ("4h.png", "1h.png", "15m.png", "5m.png", "summary.png")
NAMES = (*IMAGES, "report.json")
BYTE_LIMIT = 8 * 1024 * 1024


def png_bytes(*, size=(8, 6), color=(12, 34, 56), mode="RGB", format="PNG"):
    output = BytesIO()
    with Image.new(mode, size, color) as image:
        image.save(output, format=format)
    return output.getvalue()


def json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def bundle(*, report_id=REPORT, image_overrides=None, metadata_updates=None):
    default = png_bytes()
    images = {name: default for name in IMAGES}
    images.update(image_overrides or {})
    report = {
        "schema_version": "ctcc.trade_evidence.v1",
        "report_id": report_id,
        "images": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
            for name, data in images.items()
        },
        "synthetic": True,
    }
    report.update(metadata_updates or {})
    return images | {"report.json": json_bytes(report)}


@pytest.fixture
def storage_root(tmp_path):
    root = tmp_path / "trusted-root"
    root.mkdir()
    return root


def publish(root, files=None, **updates):
    values = {
        "report_id": REPORT,
        "files": bundle() if files is None else files,
        "clock": lambda: NOW,
    }
    return publish_evidence(root, **(values | updates))


def stored_files(root, report_id=REPORT):
    directory = root / report_id
    return {
        path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()
    }


def assert_complete(root, expected, report_id=REPORT):
    directory = root / report_id
    assert {path.name for path in directory.iterdir()} == set(NAMES)
    assert stored_files(root, report_id) == dict(expected)


def replace_report(files, mutate):
    report = json.loads(files["report.json"])
    mutate(report)
    return files | {"report.json": json_bytes(report)}


def test_publishes_exact_six_files_and_returns_actual_hashes(storage_root):
    files = bundle()
    receipt = publish(storage_root, files)
    assert receipt.status == "written"
    assert receipt.report_id == REPORT
    assert Path(receipt.report_directory) == storage_root / REPORT
    assert receipt.completed_at == NOW
    assert receipt.completed_at.tzinfo is UTC
    assert receipt.report_sha256 == hashlib.sha256(files["report.json"]).hexdigest()
    assert isinstance(receipt.files, tuple)
    assert {item.name for item in receipt.files} == set(NAMES)
    assert len(receipt.files) == len(NAMES)
    for item in receipt.files:
        assert item.sha256 == hashlib.sha256(files[item.name]).hexdigest()
        assert item.size_bytes == len(files[item.name])
    assert_complete(storage_root, files)


def test_receipt_and_its_file_records_are_frozen(storage_root):
    receipt = publish(storage_root)
    with pytest.raises(
        (FrozenInstanceError, AttributeError, TypeError, ValidationError)
    ):
        receipt.status = "already_present"
    with pytest.raises(
        (FrozenInstanceError, AttributeError, TypeError, ValidationError)
    ):
        receipt.files[0].size_bytes = 0


def test_publish_does_not_modify_the_callers_mapping(storage_root):
    files = bundle()
    before = dict(files)
    publish(storage_root, MappingProxyType(files))
    assert files == before


def test_top_level_renderer_metadata_is_preserved(storage_root):
    files = bundle(
        metadata_updates={
            "renderer": {"version": "synthetic-v1", "notes": ["測試", "no authority"]}
        }
    )
    publish(storage_root, files)
    assert_complete(storage_root, files)


def test_optional_image_metadata_does_not_change_required_digest_validation(
    storage_root,
):
    files = replace_report(
        bundle(),
        lambda report: report["images"]["4h.png"].update(
            width=8, height=6, synthetic_note="test"
        ),
    )
    publish(storage_root, files)
    assert_complete(storage_root, files)


@pytest.mark.parametrize("missing", NAMES)
def test_each_of_the_six_files_is_required_before_creating_report(
    storage_root, missing
):
    files = bundle()
    del files[missing]
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files)
    assert not (storage_root / REPORT).exists()


@pytest.mark.parametrize(
    "name",
    [
        "extra.png",
        "4H.png",
        "./4h.png",
        "../4h.png",
        "sub/4h.png",
        "sub\\4h.png",
        "4h.png:stream",
        "4h.png\x00",
        "/4h.png",
    ],
)
def test_extra_alias_or_path_like_file_names_are_rejected(storage_root, name):
    files = bundle() | {name: png_bytes()}
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files)
    assert not (storage_root / REPORT).exists()


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "value", [None, "bytes", bytearray(b"bytes"), memoryview(b"bytes"), 123]
)
def test_file_contents_must_be_exact_bytes(storage_root, name, value):
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, bundle() | {name: value})
    assert not (storage_root / REPORT).exists()


@pytest.mark.parametrize("files", [None, [], (), b"not-a-mapping", {}])
def test_invalid_mapping_does_not_publish(storage_root, files):
    with pytest.raises(EvidencePublicationError):
        publish_evidence(storage_root, report_id=REPORT, files=files, clock=lambda: NOW)
    assert not (storage_root / REPORT).exists()


@pytest.mark.parametrize(
    "report_id",
    [
        "",
        ".",
        "..",
        "../escape",
        "sub/report",
        "sub\\report",
        "C:\\escape",
        "/escape",
        "report:ads",
        "report.",
        "report ",
        " report",
        "CON",
        "con",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "LPT9",
        "nul.txt",
        "a" * 97,
        "report\x00",
        None,
        1,
        True,
    ],
)
def test_report_ids_cannot_escape_or_use_device_names(storage_root, report_id):
    files = bundle()
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files, report_id=report_id)
    assert list(storage_root.iterdir()) == []


def test_relative_root_is_rejected_before_touching_the_working_directory():
    with pytest.raises(EvidencePublicationError):
        publish(Path("synthetic-relative-evidence-root"))


def test_missing_root_is_not_implicitly_created(tmp_path):
    root = tmp_path / "missing" / "trusted-root"
    with pytest.raises(EvidencePublicationError):
        publish(root)
    assert not (tmp_path / "missing").exists()


def test_root_must_be_a_directory(tmp_path):
    root = tmp_path / "not-a-directory"
    root.write_bytes(b"preserve")
    with pytest.raises(EvidencePublicationError):
        publish(root)
    assert root.read_bytes() == b"preserve"


def test_root_with_parent_traversal_is_rejected(storage_root):
    lexical = storage_root / ".." / storage_root.name
    with pytest.raises(EvidencePublicationError):
        publish(lexical)
    assert list(storage_root.iterdir()) == []


@pytest.mark.parametrize("name", IMAGES)
def test_each_png_is_fully_validated_before_publication(storage_root, name):
    files = bundle(image_overrides={name: b"not a png"})
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files)
    assert not (storage_root / REPORT).exists()


@pytest.mark.parametrize("kind", ["truncated", "bad_crc", "jpeg_disguised"])
def test_corrupt_or_mislabeled_images_are_rejected(storage_root, kind):
    data = png_bytes()
    if kind == "truncated":
        data = data[:-16]
    elif kind == "bad_crc":
        mutable = bytearray(data)
        mutable[29] ^= 1
        data = bytes(mutable)
    else:
        data = png_bytes(format="JPEG")
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, bundle(image_overrides={"4h.png": data}))
    assert not (storage_root / REPORT).exists()


def test_animated_png_is_rejected(storage_root):
    output = BytesIO()
    with (
        Image.new("RGB", (4, 4), (0, 0, 0)) as first,
        Image.new("RGB", (4, 4), (255, 0, 0)) as second,
    ):
        first.save(
            output,
            format="PNG",
            save_all=True,
            append_images=[second],
            duration=100,
            loop=0,
        )
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, bundle(image_overrides={"4h.png": output.getvalue()}))
    assert not (storage_root / REPORT).exists()


@pytest.mark.parametrize(
    "mode,color", [("RGB", (1, 2, 3)), ("RGBA", (1, 2, 3, 255)), ("L", 127)]
)
def test_supported_static_png_color_modes_are_accepted(storage_root, mode, color):
    files = bundle(image_overrides={"4h.png": png_bytes(mode=mode, color=color)})
    publish(storage_root, files)
    assert_complete(storage_root, files)


@pytest.mark.parametrize("size", [(8193, 1), (1, 8193), (4001, 4000)])
def test_dimension_or_pixel_limit_excess_is_rejected(storage_root, size):
    data = png_bytes(size=size)
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, bundle(image_overrides={"4h.png": data}))
    assert not (storage_root / REPORT).exists()


@pytest.mark.parametrize("size", [(8192, 1), (1, 8192), (4000, 4000)])
def test_dimension_and_pixel_boundaries_are_inclusive(storage_root, size):
    files = bundle(image_overrides={"4h.png": png_bytes(size=size)})
    publish(storage_root, files)
    assert_complete(storage_root, files)


def test_png_byte_limit_is_checked_before_decoding(storage_root):
    data = png_bytes()
    oversized = data + b"x" * (BYTE_LIMIT + 1 - len(data))
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, bundle(image_overrides={"4h.png": oversized}))
    assert not (storage_root / REPORT).exists()


def test_json_byte_limit_is_inclusive(storage_root):
    files = bundle()
    files["report.json"] += b" " * (BYTE_LIMIT - len(files["report.json"]))
    receipt = publish(storage_root, files)
    assert receipt.report_sha256 == hashlib.sha256(files["report.json"]).hexdigest()
    assert_complete(storage_root, files)


def test_json_larger_than_byte_limit_is_rejected(storage_root):
    files = bundle()
    files["report.json"] += b" " * (BYTE_LIMIT + 1 - len(files["report.json"]))
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files)
    assert not (storage_root / REPORT).exists()


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{",
        b"null",
        b"[]",
        b"1",
        b"true",
        b'"text"',
        b"\xff",
        b'{"x":NaN}',
        b'{"x":Infinity}',
    ],
)
def test_invalid_json_root_encoding_and_nonfinite_values_are_rejected(
    storage_root, raw
):
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, bundle() | {"report.json": raw})
    assert not (storage_root / REPORT).exists()


@pytest.mark.parametrize("key", ["schema_version", "report_id", "images"])
def test_required_json_top_level_fields_cannot_be_omitted(storage_root, key):
    files = replace_report(bundle(), lambda report: report.pop(key))
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files)
    assert not (storage_root / REPORT).exists()


@pytest.mark.parametrize(
    "updates",
    [
        {"schema_version": "ctcc.trade_evidence.v2"},
        {"schema_version": 1},
        {"report_id": "another-report"},
        {"report_id": True},
        {"images": []},
        {"images": None},
    ],
)
def test_schema_identity_and_image_mapping_are_strict(storage_root, updates):
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, bundle(metadata_updates=updates))
    assert not (storage_root / REPORT).exists()


@pytest.mark.parametrize("name", IMAGES)
def test_manifest_requires_each_of_the_five_image_records(storage_root, name):
    files = replace_report(bundle(), lambda report: report["images"].pop(name))
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files)
    assert not (storage_root / REPORT).exists()


def test_manifest_cannot_list_extra_images(storage_root):
    files = replace_report(
        bundle(),
        lambda report: report["images"].update(
            extra={"sha256": "a" * 64, "size_bytes": 1}
        ),
    )
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha256", "a" * 64),
        ("sha256", "A" * 64),
        ("sha256", "bad"),
        ("sha256", None),
        ("sha256", 1),
        ("size_bytes", 1),
        ("size_bytes", 0),
        ("size_bytes", -1),
        ("size_bytes", True),
        ("size_bytes", 1.0),
        ("size_bytes", "1"),
    ],
)
def test_manifest_hash_and_size_must_exactly_match_bytes_and_types(
    storage_root, field, value
):
    files = replace_report(
        bundle(), lambda report: report["images"]["4h.png"].update({field: value})
    )
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files)
    assert not (storage_root / REPORT).exists()


@pytest.mark.parametrize("field", ["sha256", "size_bytes"])
def test_manifest_image_records_cannot_omit_required_keys(storage_root, field):
    files = replace_report(
        bundle(), lambda report: report["images"]["4h.png"].pop(field)
    )
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files)


@pytest.mark.parametrize("field", ["width", "height"])
@pytest.mark.parametrize("value", [0, -1, True, 1.0, "8", None])
def test_optional_image_dimensions_require_positive_exact_integers(
    storage_root, field, value
):
    files = replace_report(
        bundle(), lambda report: report["images"]["4h.png"].update({field: value})
    )
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files)


@pytest.mark.parametrize("level", ["top", "images", "image_record"])
def test_duplicate_json_keys_are_rejected_even_when_values_agree(storage_root, level):
    files = bundle()
    text = files["report.json"].decode()
    if level == "top":
        text = text[:-1] + ',"report_id":' + json.dumps(REPORT) + "}"
    elif level == "images":
        metadata = json.loads(text)["images"]["4h.png"]
        needle = '"images":{'
        text = text.replace(
            needle, needle + '"4h.png":' + json_bytes(metadata).decode() + ",", 1
        )
    else:
        digest = hashlib.sha256(files["4h.png"]).hexdigest()
        needle = '"sha256":"' + digest + '"'
        text = text.replace(needle, needle + "," + needle, 1)
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files | {"report.json": text.encode()})
    assert not (storage_root / REPORT).exists()


def test_identical_complete_publication_is_idempotent_without_rewriting(storage_root):
    files = bundle()
    first = publish(storage_root, files)
    mtimes = {name: (storage_root / REPORT / name).stat().st_mtime_ns for name in NAMES}
    second = publish(storage_root, files, clock=lambda: NOW + timedelta(seconds=1))
    assert first.status == "written"
    assert second.status == "already_present"
    assert second.completed_at == NOW + timedelta(seconds=1)
    assert second.report_sha256 == first.report_sha256
    assert {
        name: (storage_root / REPORT / name).stat().st_mtime_ns for name in NAMES
    } == mtimes
    assert_complete(storage_root, files)


def test_conflicting_complete_publication_is_preserved_not_overwritten(storage_root):
    original = bundle()
    publish(storage_root, original)
    different = bundle(image_overrides={"summary.png": png_bytes(color=(100, 20, 30))})
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, different)
    assert_complete(storage_root, original)


def test_semantically_equal_but_byte_different_json_is_a_conflict(storage_root):
    original = bundle()
    publish(storage_root, original)
    with pytest.raises(EvidencePublicationError):
        publish(
            storage_root, original | {"report.json": original["report.json"] + b"\n"}
        )
    assert_complete(storage_root, original)


@pytest.mark.parametrize("existing", [(), ("4h.png",), IMAGES, ("report.json",)])
def test_partial_existing_report_is_never_completed_or_repaired(storage_root, existing):
    files = bundle()
    directory = storage_root / REPORT
    directory.mkdir()
    for name in existing:
        (directory / name).write_bytes(files[name])
    before = stored_files(storage_root)
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files)
    assert stored_files(storage_root) == before
    assert {path.name for path in directory.iterdir()} == set(existing)


def test_unknown_file_in_an_existing_report_prevents_idempotent_acceptance(
    storage_root,
):
    files = bundle()
    publish(storage_root, files)
    extra = storage_root / REPORT / "untracked.txt"
    extra.write_bytes(b"preserve")
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files)
    assert extra.read_bytes() == b"preserve"
    assert stored_files(storage_root) == files | {"untracked.txt": b"preserve"}


def test_existing_report_path_that_is_a_file_is_preserved(storage_root):
    target = storage_root / REPORT
    target.write_bytes(b"preserve")
    with pytest.raises(EvidencePublicationError):
        publish(storage_root)
    assert target.read_bytes() == b"preserve"


def test_existing_case_aliased_file_is_not_accepted_as_exact_manifest(storage_root):
    files = bundle()
    publish(storage_root, files)
    source = storage_root / REPORT / "4h.png"
    temporary = storage_root / REPORT / "case-change.tmp"
    source.rename(temporary)
    temporary.rename(storage_root / REPORT / "4H.png")
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files)
    assert (storage_root / REPORT / "4H.png").read_bytes() == files["4h.png"]


def test_unrelated_root_contents_are_not_modified(storage_root):
    existing = storage_root / "user-note.txt"
    existing.write_bytes(b"preserve")
    publish(storage_root)
    assert existing.read_bytes() == b"preserve"


def test_clock_is_sampled_before_and_after_complete_publication(storage_root):
    files = bundle()
    calls = []

    def clock():
        calls.append(len(calls))
        if len(calls) == 1:
            assert not (storage_root / REPORT).exists()
            return NOW
        assert_complete(storage_root, files)
        return NOW + timedelta(seconds=2)

    receipt = publish(storage_root, files, clock=clock)
    assert calls == [0, 1]
    assert receipt.completed_at == NOW + timedelta(seconds=2)


def test_clock_times_are_normalized_to_utc(storage_root):
    local = NOW.astimezone(timezone(timedelta(hours=8)))
    receipt = publish(storage_root, clock=lambda: local)
    assert receipt.completed_at == NOW
    assert receipt.completed_at.tzinfo is UTC


@pytest.mark.parametrize(
    "value", [None, NOW.replace(tzinfo=None), NOW.isoformat(), 123, True]
)
def test_invalid_initial_clock_time_cannot_publish(storage_root, value):
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, clock=lambda: value)
    assert not (storage_root / REPORT).exists()


@pytest.mark.parametrize(
    "end", [NOW - timedelta(microseconds=1), NOW.replace(tzinfo=None), None]
)
def test_invalid_or_reversing_completion_clock_cannot_return_a_receipt(
    storage_root, end
):
    times = iter((NOW, end))
    files = bundle()
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files, clock=lambda: next(times))
    if (storage_root / REPORT).exists():
        assert_complete(storage_root, files)


def test_concurrent_identical_publications_never_both_claim_written(storage_root):
    files = bundle()
    barrier = Barrier(2)

    def worker():
        barrier.wait(timeout=10)
        try:
            return publish(storage_root, files).status
        except EvidencePublicationError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert results.count("written") == 1
    assert set(results) <= {"written", "already_present", "rejected"}
    assert_complete(storage_root, files)


def test_concurrent_conflicting_publications_cannot_mix_or_both_succeed(storage_root):
    first = bundle()
    second = bundle(image_overrides={"summary.png": png_bytes(color=(100, 20, 30))})
    barrier = Barrier(2)

    def worker(files):
        barrier.wait(timeout=10)
        try:
            return publish(storage_root, files).status
        except EvidencePublicationError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, (first, second)))
    assert results.count("written") == 1
    assert results.count("rejected") == 1
    saved = stored_files(storage_root)
    assert saved in (first, second)
    assert_complete(storage_root, saved)


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX symlink behavior; Windows junctions tested separately",
)
@pytest.mark.parametrize("placement", ["root", "ancestor", "report"])
def test_posix_directory_symlinks_are_rejected_without_following(tmp_path, placement):
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "marker.txt").write_bytes(b"preserve")
    root = tmp_path / "trusted"
    if placement == "root":
        root.symlink_to(actual, target_is_directory=True)
    elif placement == "ancestor":
        (actual / "child").mkdir()
        root.symlink_to(actual, target_is_directory=True)
        root = root / "child"
    else:
        root.mkdir()
        (root / REPORT).symlink_to(actual, target_is_directory=True)
    with pytest.raises(EvidencePublicationError):
        publish(root)
    assert (actual / "marker.txt").read_bytes() == b"preserve"
    assert not (actual / REPORT).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific junction coverage")
@pytest.mark.parametrize("placement", ["root", "ancestor", "report"])
def test_windows_junctions_are_rejected_without_touching_the_target(
    tmp_path, placement
):
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "marker.txt").write_bytes(b"preserve")
    trusted = tmp_path / "trusted"
    if placement == "report":
        trusted.mkdir()
        junction = trusted / REPORT
        root = trusted
    else:
        junction = trusted
        root = junction
        if placement == "ancestor":
            (actual / "child").mkdir()
            root = junction / "child"
    junction_text = str(junction).replace("'", "''")
    actual_text = str(actual).replace("'", "''")
    command = f"$ErrorActionPreference = 'Stop'; New-Item -ItemType Junction -Path '{junction_text}' -Target '{actual_text}' | Out-Null"
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, (
        "Windows junction fixture creation failed; this security coverage was not skipped: "
        + completed.stderr
    )
    try:
        with pytest.raises(EvidencePublicationError, match="reparse"):
            publish(root)
        assert (actual / "marker.txt").read_bytes() == b"preserve"
        assert not (actual / REPORT).exists()
        assert not any(actual.glob("*.png"))
        if placement == "ancestor":
            assert list((actual / "child").iterdir()) == []
    finally:
        # Removing the test-owned junction itself does not delete its target.
        junction.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows UNC and device path syntax")
@pytest.mark.parametrize(
    "root",
    [
        Path(r"\\synthetic.invalid\share\evidence"),
        Path(r"\\?\C:\synthetic-evidence"),
        Path(r"\\.\C:\synthetic-evidence"),
    ],
)
def test_unc_and_device_paths_are_rejected_lexically_before_filesystem_io(root):
    with pytest.raises(EvidencePublicationError, match="UNC and device"):
        publish(root)


def test_png_trailing_bytes_after_iend_are_not_accepted(storage_root):
    data = png_bytes() + b"untracked trailer"
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, bundle(image_overrides={"4h.png": data}))
    assert not (storage_root / REPORT).exists()


@pytest.mark.parametrize("field,value", [("width", 9), ("height", 7)])
def test_manifest_dimensions_must_match_decoded_pixels(storage_root, field, value):
    files = replace_report(
        bundle(), lambda report: report["images"]["4h.png"].update({field: value})
    )
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files)
    assert not (storage_root / REPORT).exists()


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_optional_metadata_is_rejected_in_an_otherwise_valid_manifest(
    storage_root, literal
):
    files = bundle()
    files["report.json"] = (
        files["report.json"][:-1] + b',"renderer_extra":' + literal.encode() + b"}"
    )
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files)
    assert not (storage_root / REPORT).exists()


def test_multilink_existing_file_is_not_accepted_as_immutable_evidence(
    storage_root, tmp_path
):
    files = bundle()
    publish(storage_root, files)
    original = storage_root / REPORT / "4h.png"
    extra_link = tmp_path / "test-owned-hardlink.png"
    assert extra_link.parent == tmp_path
    os.link(original, extra_link)
    assert original.stat().st_nlink > 1
    with pytest.raises(EvidencePublicationError):
        publish(storage_root, files)
    assert original.read_bytes() == extra_link.read_bytes() == files["4h.png"]
    assert_complete(storage_root, files)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows directory handle sharing")
@pytest.mark.parametrize("placement", ["ancestor", "root", "report"])
def test_native_windows_handles_block_rename_until_receipt_completion(
    tmp_path, placement
):
    scope = tmp_path / "test-owned-scope"
    root = scope / "trusted-root"
    root.mkdir(parents=True)
    source = (
        scope
        if placement == "ancestor"
        else root
        if placement == "root"
        else root / REPORT
    )
    destination = source.with_name(source.name + "-moved")
    # Every move is exactly between two paths in this invocation's owned tmpdir.
    source.relative_to(tmp_path)
    destination.relative_to(tmp_path)
    assert not destination.exists()
    if placement == "report":
        source.mkdir()
    source.rename(destination)
    destination.rename(source)
    if placement == "report":
        source.rmdir()  # Empty test-created control directory, never published data.
    attempts = []
    calls = 0

    def clock():
        nonlocal calls
        calls += 1
        if calls == 2:
            try:
                source.rename(destination)
            except OSError as exc:
                attempts.append(exc.winerror)
            else:
                attempts.append("rename_unexpectedly_succeeded")
                destination.rename(source)
        return NOW

    files = bundle()
    receipt = publish(root, files, clock=clock)
    assert receipt.status == "written"
    assert calls == 2
    assert len(attempts) == 1
    assert attempts[0] in {5, 32}, attempts
    assert_complete(root, files)
    # All pinned handles must be released when the caller receives the receipt.
    source.rename(destination)
    destination.rename(source)
    assert_complete(root, files)


class UnexpectedPublicationIO(BaseException):
    """Escapes the publisher's Exception wrapper to expose false-green rejects."""


@pytest.mark.skipif(os.name != "nt", reason="Native Windows owned-directory primitive")
def test_native_windows_owned_root_handle_enforces_publisher_and_rename_exclusion(
    storage_root, tmp_path
):
    source = storage_root
    destination = source.with_name(source.name + "-primitive-moved")
    source.relative_to(tmp_path)
    destination.relative_to(tmp_path)
    assert not destination.exists()
    # A positive baseline distinguishes handle exclusion from an unrelated ACL.
    source.rename(destination)
    destination.rename(source)
    api = storage_module._WindowsAPI()
    handle = api.open_directory(source, publisher=True)
    second_handle = None
    try:
        try:
            second_handle = api.open_directory(source, publisher=True)
        except OSError as exc:
            assert exc.winerror == 32, f"Expected sharing violation, got {exc!r}"
        else:
            pytest.fail("a second publisher acquired the already-leased owned root")
        try:
            source.rename(destination)
        except OSError as exc:
            assert exc.winerror in {5, 32}, f"Unexpected rename refusal: {exc!r}"
        else:
            destination.rename(source)
            pytest.fail("the owned root could be renamed while its handle was held")
    finally:
        if second_handle is not None:
            api.close(second_handle)
        api.close(handle)
    source.rename(destination)
    destination.rename(source)
    assert source.is_dir()
    assert not destination.exists()


@pytest.fixture
def forbid_publication_io(monkeypatch):
    def forbidden_context(*args, **kwargs):
        raise UnexpectedPublicationIO("pre-IO validation reached filesystem handling")

    # This fixture is only for pure preflight tests, not native filesystem tests.
    # Do not alter platform detection or pretend to exercise Windows/POSIX APIs.
    monkeypatch.setattr(storage_module, "_windows_root", forbidden_context)
    monkeypatch.setattr(storage_module, "_posix_root", forbidden_context)


def test_pre_io_control_proves_valid_packet_reaches_the_forbidden_io_boundary(
    forbid_publication_io,
):
    with pytest.raises(UnexpectedPublicationIO):
        publish(Path.cwd())


@pytest.mark.parametrize(
    "case",
    [
        "missing_png",
        "missing_json",
        "extra_file",
        "case_alias",
        "not_bytes",
        "png_signature",
        "png_crc",
        "png_truncated",
        "png_trailer",
        "jpeg_as_png",
        "png_too_wide",
        "png_too_tall",
        "png_too_many_pixels",
        "png_too_many_bytes",
        "json_too_many_bytes",
        "json_invalid_utf8",
        "json_array",
        "json_duplicate_top",
        "json_duplicate_record",
        "json_nonfinite_metadata",
        "missing_schema",
        "wrong_schema",
        "wrong_report_id",
        "missing_image",
        "extra_image",
        "wrong_digest",
        "wrong_size",
        "boolean_size",
        "float_size",
        "missing_digest",
        "missing_size",
        "wrong_width",
        "wrong_height",
        "boolean_width",
        "zero_height",
        "device_report_id",
        "traversal_report_id",
        "case_device_report_id",
        "relative_root",
        "invalid_initial_clock",
    ],
)
def test_pre_io_invalid_packets_are_rejected_before_any_directory_access(
    forbid_publication_io, case
):
    files = bundle()
    report_id = REPORT
    root = Path.cwd()
    clock = lambda: NOW
    report = json.loads(files["report.json"])
    if case == "missing_png":
        del files["4h.png"]
    elif case == "missing_json":
        del files["report.json"]
    elif case == "extra_file":
        files["extra.png"] = png_bytes()
    elif case == "case_alias":
        files["4H.png"] = files.pop("4h.png")
    elif case == "not_bytes":
        files["4h.png"] = bytearray(files["4h.png"])
    elif case.startswith("png_") or case == "jpeg_as_png":
        data = png_bytes()
        if case == "png_signature":
            data = b"not PNG"
        elif case == "png_crc":
            mutable = bytearray(data)
            mutable[29] ^= 1
            data = bytes(mutable)
        elif case == "png_truncated":
            data = data[:-16]
        elif case == "png_trailer":
            data += b"untracked trailer"
        elif case == "jpeg_as_png":
            data = png_bytes(format="JPEG")
        elif case == "png_too_wide":
            data = png_bytes(size=(8193, 1))
        elif case == "png_too_tall":
            data = png_bytes(size=(1, 8193))
        elif case == "png_too_many_pixels":
            data = png_bytes(size=(4001, 4000))
        elif case == "png_too_many_bytes":
            data += b"x" * (BYTE_LIMIT + 1 - len(data))
        files = bundle(image_overrides={"4h.png": data})
    elif case == "json_too_many_bytes":
        files["report.json"] += b" " * (BYTE_LIMIT + 1 - len(files["report.json"]))
    elif case == "json_invalid_utf8":
        files["report.json"] = b"\xff"
    elif case == "json_array":
        files["report.json"] = b"[]"
    elif case == "json_duplicate_top":
        files["report.json"] = (
            files["report.json"][:-1] + b',"report_id":' + json_bytes(REPORT) + b"}"
        )
    elif case == "json_duplicate_record":
        needle = b'"sha256":' + json_bytes(report["images"]["4h.png"]["sha256"])
        files["report.json"] = files["report.json"].replace(
            needle, needle + b"," + needle, 1
        )
    elif case == "json_nonfinite_metadata":
        files["report.json"] = files["report.json"][:-1] + b',"extra":NaN}'
    elif case in {"device_report_id", "case_device_report_id", "traversal_report_id"}:
        report_id = {
            "device_report_id": "CON",
            "case_device_report_id": "nUl",
            "traversal_report_id": "../escape",
        }[case]
    elif case == "relative_root":
        root = Path("synthetic-relative-root")
    elif case == "invalid_initial_clock":
        clock = lambda: NOW.replace(tzinfo=None)
    else:
        image_record = report["images"]["4h.png"]
        if case == "missing_schema":
            del report["schema_version"]
        elif case == "wrong_schema":
            report["schema_version"] = "unsupported"
        elif case == "wrong_report_id":
            report["report_id"] = "another-report"
        elif case == "missing_image":
            del report["images"]["4h.png"]
        elif case == "extra_image":
            report["images"]["extra.png"] = dict(image_record)
        elif case == "missing_digest":
            del image_record["sha256"]
        elif case == "missing_size":
            del image_record["size_bytes"]
        else:
            field, value = {
                "wrong_digest": ("sha256", "a" * 64),
                "wrong_size": ("size_bytes", len(files["4h.png"]) + 1),
                "boolean_size": ("size_bytes", True),
                "float_size": ("size_bytes", float(len(files["4h.png"]))),
                "wrong_width": ("width", 9),
                "wrong_height": ("height", 7),
                "boolean_width": ("width", True),
                "zero_height": ("height", 0),
            }[case]
            image_record[field] = value
        files["report.json"] = json_bytes(report)
    with pytest.raises(EvidencePublicationError):
        publish(root, files, report_id=report_id, clock=clock)
