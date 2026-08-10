from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys


TEXT_SUFFIXES = {
    ".example",
    ".ini",
    ".mako",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_NAMES = {"Dockerfile", ".dockerignore", ".gitignore"}
EXCLUDED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".idea",
    ".vscode",
    "__pycache__",
    "backups",
    "htmlcov",
    "node_modules",
    "pytest-of-root",
    "reports",
}
EXCLUDED_FILES = {".coverage", ".env", "MANIFEST.sha256", "uv.lock"}
EXCLUDED_SUFFIXES = {".bak", ".dump", ".log", ".patch", ".pyc", ".pyo", ".zip"}


def _excluded(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in EXCLUDED_DIRECTORIES or part.endswith(".egg-info") for part in relative.parts[:-1]):
        return True
    return (
        path.name in EXCLUDED_FILES
        or (path.name.startswith(".env.") and path.name != ".env.example")
        or ".backup-" in path.name
        or path.suffix.lower() in EXCLUDED_SUFFIXES
    )


def source_files(root: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and not path.is_symlink() and not _excluded(path, root)
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def canonical_digest(path: Path) -> str:
    data = path.read_bytes()
    if path.name in TEXT_NAMES or path.suffix.lower() in TEXT_SUFFIXES:
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()


def build_manifest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): canonical_digest(path)
        for path in source_files(root)
    }


def write_manifest(root: Path, manifest_path: Path) -> None:
    entries = build_manifest(root)
    text = "".join(f"{digest}  {name}\n" for name, digest in entries.items())
    manifest_path.write_text(text, encoding="utf-8", newline="\n")


def read_manifest(manifest_path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for number, raw_line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            digest, name = raw_line.split("  ", 1)
        except ValueError as exc:
            raise ValueError(f"invalid manifest line {number}") from exc
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"invalid SHA256 on manifest line {number}")
        if not name or name in entries:
            raise ValueError(f"invalid or duplicate path on manifest line {number}")
        entries[name] = digest
    return entries


def check_manifest(root: Path, manifest_path: Path) -> bool:
    expected = read_manifest(manifest_path)
    actual = build_manifest(root)
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(
        name for name in set(expected) & set(actual) if expected[name] != actual[name]
    )
    for name in missing:
        print(f"MISSING {name}")
    for name in extra:
        print(f"EXTRA {name}")
    for name in changed:
        print(f"CHANGED {name}")
    if missing or extra or changed:
        print(
            f"manifest check failed: missing={len(missing)} extra={len(extra)} changed={len(changed)}"
        )
        return False
    print(f"manifest check passed: {len(actual)} files")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Write or verify canonical CTCC source hashes")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    manifest_path = (args.manifest or root / "MANIFEST.sha256").resolve()
    if args.write:
        write_manifest(root, manifest_path)
        print(f"wrote {manifest_path}")
        return 0
    return 0 if check_manifest(root, manifest_path) else 1


if __name__ == "__main__":
    sys.exit(main())
