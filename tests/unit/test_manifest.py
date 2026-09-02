from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "manifest.py"
SPEC = spec_from_file_location("ctcc_manifest", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
manifest = module_from_spec(SPEC)
SPEC.loader.exec_module(manifest)


def test_manifest_is_cross_platform_and_detects_changes(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    source = tmp_path / "app" / "sample.py"
    source.write_bytes(b"print('ok')\r\n")
    manifest_path = tmp_path / "MANIFEST.sha256"

    manifest.write_manifest(tmp_path, manifest_path)
    first_digest = manifest.read_manifest(manifest_path)["app/sample.py"]
    source.write_bytes(b"print('ok')\n")

    assert manifest.canonical_digest(source) == first_digest
    assert manifest.check_manifest(tmp_path, manifest_path) is True

    source.write_text("print('changed')\n", encoding="utf-8")
    assert manifest.check_manifest(tmp_path, manifest_path) is False


def test_manifest_normalizes_alembic_mako_line_endings(tmp_path: Path) -> None:
    template = tmp_path / "script.py.mako"
    template.write_bytes(b"revision = ${repr(up_revision)}\r\n")
    first_digest = manifest.canonical_digest(template)

    template.write_bytes(b"revision = ${repr(up_revision)}\n")

    assert manifest.canonical_digest(template) == first_digest


def test_manifest_normalizes_json_line_endings(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(b'{"execution_authority":false}\r\n')
    first_digest = manifest.canonical_digest(receipt)

    receipt.write_bytes(b'{"execution_authority":false}\n')

    assert manifest.canonical_digest(receipt) == first_digest


def test_manifest_excludes_secrets_build_products_and_archives(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=value\n", encoding="utf-8")
    (tmp_path / ".git").write_text("gitdir: /tmp/worktrees/example\n", encoding="utf-8")
    (tmp_path / "delivery.patch").write_text("patch\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "installed.py").write_text("pass\n", encoding="utf-8")

    entries = manifest.build_manifest(tmp_path)

    assert entries.keys() == {"app.py"}
