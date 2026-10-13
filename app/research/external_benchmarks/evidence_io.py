from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024


class ExternalEvidenceIOError(RuntimeError):
    pass


def preflight_unique_json(payload: bytes, *, maximum: int = MAX_EVIDENCE_BYTES) -> None:
    """Reject ambiguous JSON before Pydantic interprets pinned source bytes."""

    if type(payload) is not bytes or not 0 < len(payload) <= maximum:
        raise ExternalEvidenceIOError("evidence JSON byte size is invalid")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ExternalEvidenceIOError("evidence JSON repeats a key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ExternalEvidenceIOError("evidence JSON contains a non-JSON number")

    try:
        json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            parse_float=Decimal,
        )
    except ExternalEvidenceIOError:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ExternalEvidenceIOError("evidence JSON is invalid") from exc


def real_directory(path: Path, name: str) -> Path:
    if path.is_symlink():
        raise ExternalEvidenceIOError(f"{name} cannot be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ExternalEvidenceIOError(f"{name} does not exist") from exc
    if not resolved.is_dir():
        raise ExternalEvidenceIOError(f"{name} must be a real directory")
    return resolved


def evidence_path(
    root: Path,
    relative_path: str,
    *,
    create_parents: bool = True,
) -> Path:
    resolved_root = real_directory(root, "evidence root")
    if "\\" in relative_path:
        raise ExternalEvidenceIOError("evidence paths must use POSIX separators")
    parts = PurePosixPath(relative_path).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ExternalEvidenceIOError("evidence path is not a safe relative path")
    parent = resolved_root
    for part in parts[:-1]:
        parent = parent / part
        if parent.is_symlink():
            raise ExternalEvidenceIOError("evidence path cannot traverse a symlink")
        if parent.exists() and not parent.is_dir():
            raise ExternalEvidenceIOError("evidence path parent is not a directory")
        if create_parents:
            parent.mkdir(exist_ok=True)
        elif not parent.exists():
            raise ExternalEvidenceIOError("evidence path parent does not exist")
    resolved_parent = parent.resolve(strict=True)
    if not resolved_parent.is_relative_to(resolved_root):
        raise ExternalEvidenceIOError("evidence path escaped its root")
    candidate = resolved_parent / parts[-1]
    if candidate.is_symlink():
        raise ExternalEvidenceIOError("evidence output cannot be a symlink")
    return candidate


def write_contract_json(
    root: Path,
    relative_path: str,
    model: BaseModel,
) -> str:
    """Write a frozen contract without replacing any existing evidence."""

    destination = evidence_path(root, relative_path)
    payload = (model.model_dump_json(indent=2) + "\n").encode("utf-8")
    if len(payload) > MAX_EVIDENCE_BYTES:
        raise ExternalEvidenceIOError("evidence JSON exceeds its byte limit")
    if destination.exists():
        if not destination.is_file():
            raise ExternalEvidenceIOError(
                "evidence output already exists and is not a file"
            )
        try:
            existing = destination.read_bytes()
        except OSError as exc:
            raise ExternalEvidenceIOError(
                "existing evidence could not be read"
            ) from exc
        if existing != payload:
            raise ExternalEvidenceIOError(
                "existing evidence differs from the reviewed output"
            )
        return "already_present"

    partial: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".partial",
            delete=False,
        ) as handle:
            partial = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(partial, destination, follow_symlinks=False)
        except FileExistsError as exc:
            raise ExternalEvidenceIOError(
                "evidence destination appeared during the write"
            ) from exc
        partial.unlink()
        partial = None
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)
    return "written"


def read_contract_json[ModelT: BaseModel](
    root: Path,
    relative_path: str,
    model_type: type[ModelT],
) -> ModelT:
    path = evidence_path(root, relative_path, create_parents=False)
    if not path.exists() or not path.is_file():
        raise ExternalEvidenceIOError("evidence input must be a real file")
    if path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise ExternalEvidenceIOError("evidence input exceeds its byte limit")
    try:
        with path.open("rb") as handle:
            payload = handle.read(MAX_EVIDENCE_BYTES + 1)
    except OSError as exc:
        raise ExternalEvidenceIOError("evidence input could not be read") from exc
    preflight_unique_json(payload)
    return model_type.model_validate_json(payload)
