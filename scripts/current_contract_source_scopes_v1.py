"""Explicit actual-source and historical-database scopes; never relabel a head.

A final source54 checkout can create a fresh historical52/53 database for the
unchanged component contracts. Full source/manifest identity and every original
migration-prefix byte remain checked. These scopes are not schema admission.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from scripts import manifest
from scripts.verify_migration_identity import database_head, source_identity

_PREFIX = {
    "0052": ("5ab13f675830c4bde4207f041f939225b48822cc5719ce57294e3d4c6ddad576", 53),
    "0053": ("66d7632eb522f9a295b7c509898ad01102756f7e1d748c847829248f2970d44d", 54),
}
_TAIL = {
    "0053": (
        "migrations/versions/0053_collector_current_revision_lineage.py",
        "ef36db2b1016d078e12d995e84f39654502f3c098e52fa3e826112b88dc17269",
        "0052",
    ),
    "0054": (
        "migrations/versions/0054_collector_issued_account_revisions.py",
        "a94f49ad17af5e37421c30e6044b3ffa5cfa51d75c70cedc60de67cabe83ac47",
        "0053",
    ),
}
_SUPPORTED = {
    "0052": frozenset(("0052",)),
    "0053": frozenset(("0052", "0053")),
    "0054": frozenset(("0052", "0053", "0054")),
}


class ContractSourceScopeError(ValueError):
    pass


def _deny():
    raise ContractSourceScopeError(
        "contract_source_or_historical_scope_changed"
    ) from None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class ContractSourceScopeV1:
    actual_source_head: str
    database_target: str
    actual_migration_sha256: str
    historical_prefix_sha256: str
    manifest_sha256: str
    account_complete: bool = False
    execution_authority: bool = False


def _scope(root, target):
    root = Path(root)
    if root.is_symlink() or not manifest.check_manifest(root, root / "MANIFEST.sha256"):
        _deny()
    before = source_identity(root)
    if before.head not in _SUPPORTED or target not in _SUPPORTED[before.head]:
        _deny()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option(
        "script_location", str(root / "migrations").replace("%", "%%")
    )
    graph = ScriptDirectory.from_config(config)
    if (
        tuple(graph.get_heads()) != (before.head,)
        or graph.get_revision("0052").down_revision != "0051"
    ):
        _deny()
    for head, (relative, pin, previous) in _TAIL.items():
        if head <= before.head:
            path = root / relative
            if (
                path.is_symlink()
                or not path.is_file()
                or _sha(path.read_bytes()) != pin
                or graph.get_revision(head).down_revision != previous
            ):
                _deny()
    # The old prefix remains the exact original env.py plus every old migration,
    # including extra .py files: missing/extra/renamed/changed rows alter its pin.
    files = {}
    for path in sorted((root / "migrations").rglob("*.py")):
        if path.is_symlink() or not path.is_file():
            _deny()
        relative = path.relative_to(root).as_posix()
        if any(relative == item[0] and head > target for head, item in _TAIL.items()):
            continue
        files[relative] = _sha(path.read_bytes())
    prefix = _sha(_canonical(files))
    expected_prefix, expected_count = _PREFIX["0053" if target == "0054" else target]
    if target == "0054":
        # Preserve the complete genuine53 prefix independently of the additive54.
        old = {name: pin for name, pin in files.items() if name != _TAIL["0054"][0]}
        if _sha(_canonical(old)) != expected_prefix or len(old) != expected_count:
            _deny()
    elif prefix != expected_prefix or len(files) != expected_count:
        _deny()
    manifest_pin = _sha((root / "MANIFEST.sha256").read_bytes())
    if before != source_identity(root) or not manifest.check_manifest(
        root, root / "MANIFEST.sha256"
    ):
        _deny()
    return ContractSourceScopeV1(
        before.head, target, before.sha256, prefix, manifest_pin
    )


def historical0052_source_scope(root):
    return _scope(root, "0052")


def historical0053_source_scope(root):
    return _scope(root, "0053")


def actual_current_source_scope(root):
    # Select only the exact source head independently measured from this tree.
    # A historical child target is never used as a primary harness identity.
    measured = source_identity(Path(root))
    if measured.head not in _SUPPORTED:
        _deny()
    scope = _scope(root, measured.head)
    if scope.actual_migration_sha256 != measured.sha256:
        _deny()
    return scope


def current0054_source_scope(root):
    scope = _scope(root, "0054")
    if scope.actual_source_head != "0054":
        _deny()
    return scope


def verify_database_scope(connection, root, scope):
    # A caller-created DTO cannot substitute for independently re-reading source.
    if (
        type(scope) is not ContractSourceScopeV1
        or scope.account_complete is not False
        or scope.execution_authority is not False
    ):
        _deny()
    expected = _scope(root, scope.database_target)
    if scope != expected or database_head(connection) != scope.database_target:
        _deny()
    if _scope(root, scope.database_target) != expected:
        _deny()
    return {
        "schema": "ctcc.actual_source_historical_database_scope.v1",
        "source_head": scope.actual_source_head,
        "database_head": scope.database_target,
        "migration_sha256": scope.actual_migration_sha256,
        "historical_prefix_sha256": scope.historical_prefix_sha256,
        "manifest_sha256": scope.manifest_sha256,
        "account_complete": False,
        "execution_authority": False,
    }
