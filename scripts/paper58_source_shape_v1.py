"""Closed source58 DATA shape; no installed schema or actor admission."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PATH = "tests/fixtures/paper58_source_shape_v1.json"
SHA256 = "afba325cf8a65d7b07a3b0abe237e8bc3a13b6fb10c600b6f89f6ac1e3e19400"


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _need(value):
    if value is not True:
        raise ValueError("paper58_source_shape_refused") from None


def source_shape_v1():
    path = ROOT / PATH
    _need(path.is_file() and not path.is_symlink() and path.stat().st_size <= 256000)
    raw = path.read_bytes()
    _need(hashlib.sha256(raw).hexdigest() == SHA256)
    value = json.loads(raw)
    _need(canonical(value) == raw)
    _need(
        value["schema"] == "ctcc.paper58.source_shape_data.v1"
        and value["head"] == "0058"
        and type(value["column_names"]) is dict
        and len(value["column_names"]) == 105
        and type(value["functions"]) is list
        and len(value["functions"]) == len(set(value["functions"])) == 161
        and value["complete_schema_admission"] is False
        and value["paper_startup_admission"] is False
        and value["execution_authority"] is False
    )
    sources = value["source_files_canonical_lf"]
    _need(type(sources) is dict and len(sources) == 134)
    _need(
        hashlib.sha256(canonical(sources)).hexdigest() == value["source_scope_sha256"]
    )
    for name, expected in sources.items():
        relative = PurePosixPath(name)
        _need(
            type(name) is str
            and relative.parts
            and not relative.is_absolute()
            and ".." not in relative.parts
            and "\\" not in name
            and ":" not in name
        )
        source = ROOT.joinpath(*relative.parts)
        _need(source.is_file() and not source.is_symlink())
        _need(
            hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            == expected
        )
    return value
