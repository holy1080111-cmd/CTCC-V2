from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

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
    for generated in ("build", "dist"):
        (tmp_path / generated).mkdir()
        (tmp_path / generated / "generated.py").write_text("pass\n", encoding="utf-8")

    entries = manifest.build_manifest(tmp_path)

    assert entries.keys() == {"app.py"}


def test_runtime_trade_evidence_is_not_part_of_the_source_release(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text("pass\n", encoding="utf-8")
    packet = tmp_path / "artifacts" / "trade_evidence" / "synthetic-report"
    packet.mkdir(parents=True)
    (packet / "report.json").write_text('{"synthetic": true}', encoding="utf-8")
    (packet / "summary.png").write_bytes(b"synthetic-png-not-a-real-trade")

    assert manifest.build_manifest(tmp_path).keys() == {"app.py"}


def test_validation_results_are_not_part_of_the_source_manifest(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text("pass\n", encoding="utf-8")
    report = tmp_path / "validation-results" / "run-1" / "junit.xml"
    report.parent.mkdir(parents=True)
    report.write_text("<testsuite tests='1' />\n", encoding="utf-8")

    assert manifest.build_manifest(tmp_path).keys() == {"app.py"}


@pytest.mark.parametrize(
    "name",
    [
        ".envrc",
        ".ENV.live",
        ".ENV.example",
        "config/secret.TOKEN",
        "PRIVATE-NOTION/binding.json",
        "nested/Private-Notion/receipt.json",
        "REPORTS/private.json",
        "BACKUPS/account.json",
    ],
)
def test_manifest_excludes_private_paths_independent_of_case(tmp_path, name):
    private = tmp_path / name
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_bytes(b"synthetic-only")
    assert manifest.build_manifest(tmp_path) == {}


def test_manifest_preserves_only_exact_env_example_name(tmp_path):
    (tmp_path / ".env.example").write_bytes(b"SYNTHETIC_ONLY=\n")
    assert manifest.build_manifest(tmp_path).keys() == {".env.example"}


@pytest.mark.parametrize(
    "name",
    [
        ".key",
        "secret.key",
        "nested/client.p12",
        "client.pfx",
        "id_rsa",
        "nested/id_ed25519",
    ],
)
def test_manifest_fails_closed_on_private_key_file_names(tmp_path: Path, name: str):
    private = tmp_path / name
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_bytes(b"synthetic-only")

    with pytest.raises(ValueError, match="source private key material is forbidden"):
        manifest.build_manifest(tmp_path)


def test_manifest_keeps_public_pem_but_rejects_private_pem_material(tmp_path: Path):
    public = tmp_path / "public-certificate.pem"
    public.write_bytes(b"-----BEGIN CERTIFICATE-----\nsynthetic-only\n")
    public_key = tmp_path / "id_rsa.pub"
    public_key.write_bytes(b"synthetic-public-key")
    assert manifest.build_manifest(tmp_path).keys() == {
        "id_rsa.pub",
        "public-certificate.pem",
    }

    private = tmp_path / "neutral-name.pem"
    private.write_bytes(b"-----BEGIN " + b"PRIVATE KEY-----\nsynthetic-only\n")
    with pytest.raises(ValueError, match="source private key material is forbidden"):
        manifest.build_manifest(tmp_path)


def test_manifest_rejects_unlisted_source_symlink(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("pass\n", encoding="utf-8")
    manifest_path = tmp_path / "MANIFEST.sha256"
    manifest.write_manifest(tmp_path, manifest_path)
    try:
        (tmp_path / "alias.py").symlink_to(source)
    except OSError:
        pytest.skip("source symlink creation requires OS privilege")

    with pytest.raises(ValueError, match="source symlink or junction cannot be hashed"):
        manifest.check_manifest(tmp_path, manifest_path)
