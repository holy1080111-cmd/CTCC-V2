"""Route only explicit newer-source hermetic contracts; no validation bypass.

The selected runner still admits exact archive/commit/tree/manifest bytes and
the selected union still re-reads every actual evidence artifact. Dispatch
neither grants migration/schema/native admission nor accepts a success flag.
"""

import hashlib
import io
import re
import tarfile
from pathlib import Path, PurePosixPath

from scripts.evidence_json import read_evidence_json


def _argument(argv, flag, *, equals=False):
    found = [
        index
        for index, value in enumerate(argv)
        if value == flag or (equals and value.startswith(flag + "="))
    ]
    if not found:
        return None
    if len(found) != 1:
        raise ValueError("hermetic_contract_dispatch_argument_invalid")
    index = found[0]
    if argv[index] != flag:
        value = argv[index][len(flag) + 1 :]
        if value:
            return value
    elif index + 1 < len(argv) and not argv[index + 1].startswith("--"):
        return argv[index + 1]
    raise ValueError("hermetic_contract_dispatch_argument_invalid")


def _archive_head(archive):
    revisions = []
    for member in archive.getmembers():
        parts = PurePosixPath(member.name).parts
        if (
            member.isfile()
            and len(parts) == 3
            and parts[:2] == ("migrations", "versions")
        ):
            revision = re.fullmatch(r"(\d{4})_[A-Za-z0-9_]+\.py", parts[2])
            if revision is not None:
                revisions.append(int(revision.group(1)))
    head = max(revisions, default=0)
    if head > 55 or (head >= 54 and revisions.count(head) != 1):
        raise ValueError("hermetic_contract_dispatch_migration_unsupported")
    return head


def capture_hermetic_archive(path: Path, archive_sha256: str) -> bytes:
    """Capture the legacy-compatible input once; selected runners revalidate it."""
    if (
        path.is_symlink()
        or not path.is_file()
        or re.fullmatch("[a-f0-9]{64}", archive_sha256) is None
    ):
        raise ValueError("hermetic_contract_dispatch_archive_invalid")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != archive_sha256:
        raise ValueError("hermetic_contract_dispatch_archive_changed")
    return raw


def dispatch_captured_hermetic(archive_bytes):
    # Member names select a strict versioned runner only; they never admit the
    # archive. The runner independently rechecks the same captured bytes.
    with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as archive:
        head = _archive_head(archive)
    if head < 54:
        return None
    if head == 55:
        from scripts.verify_current0055_hermetic import main
    else:
        from scripts.verify_current0054_hermetic import main

    result = main(archive_bytes=archive_bytes)
    return 0 if result is None else result


def maybe_dispatch_hermetic(argv):
    archive_name = _argument(argv, "--archive", equals=True)
    archive_sha256 = _argument(argv, "--archive-sha256", equals=True)
    if archive_name is None or archive_sha256 is None:
        return None
    return dispatch_captured_hermetic(
        capture_hermetic_archive(Path(archive_name), archive_sha256)
    )


def maybe_dispatch_union(argv):
    artifacts_name = _argument(argv, "--artifacts")
    if artifacts_name is None:
        return None
    artifacts = Path(artifacts_name)
    if artifacts.is_symlink() or not artifacts.is_dir():
        raise ValueError("hermetic_contract_dispatch_artifacts_invalid")
    commit = _argument(argv, "--commit")
    tree = _argument(argv, "--tree")
    if any(
        type(value) is not str or re.fullmatch("[a-f0-9]{40}", value) is None
        for value in (commit, tree)
    ):
        raise ValueError("hermetic_contract_dispatch_git_identity_required")
    from scripts.verify_final_hermetic import archive_tree

    heads = set()
    for attachment in artifacts.iterdir():
        if not attachment.is_dir():
            continue
        if attachment.is_symlink():
            raise ValueError("hermetic_contract_dispatch_artifact_symlink")
        identity_path = attachment / "ctcc-validation" / "identity.json"
        if not identity_path.is_file():
            continue
        if (
            identity_path.is_symlink()
            or not 0 < identity_path.stat().st_size <= 2 * 1024 * 1024
        ):
            raise ValueError("hermetic_contract_dispatch_identity_invalid")
        identity = read_evidence_json(identity_path.read_bytes())
        if type(identity) is not dict:
            raise ValueError("hermetic_contract_dispatch_identity_invalid")
        # Version markers in evidence are untrusted and may have been removed.
        # Bind the uploaded archive to the caller's independently pinned Git
        # identity before using its actual migration names to select a verifier.
        archive_path = attachment / "ctcc-source.tar"
        if archive_path.is_symlink() or not archive_path.is_file():
            raise ValueError("hermetic_contract_dispatch_union_archive_missing")
        raw = archive_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != identity.get("archive_sha256"):
            raise ValueError("hermetic_contract_dispatch_union_archive_changed")
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            if (
                archive.pax_headers.get("comment") != commit
                or archive_tree(archive) != tree
            ):
                raise ValueError(
                    "hermetic_contract_dispatch_union_archive_identity_changed"
                )
            heads.add(_archive_head(archive))
    if not heads or max(heads) < 54:
        return None
    if len(heads) != 1:
        raise ValueError("hermetic_contract_dispatch_mixed_migration_heads")
    if heads == {55}:
        from scripts.verify_current0055_linux_union import main
    else:
        from scripts.verify_current0054_linux_union import main

    result = main()
    return 0 if result is None else result
