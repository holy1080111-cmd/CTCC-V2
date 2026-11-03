"""Reconstruct the exact uploaded public archive for current54 union readback.

CI uploads the archive and evidence, not an extracted source directory. This
helper admits the archive's commit/tree/file map before temporary extraction;
all catalog, source-scope and native-baseline gates still run independently.
"""

import hashlib
import io
import tarfile
import tempfile
from pathlib import Path

from scripts import collector_current_0054_toolchain as current54
from scripts import current_contract_source_scopes_v1 as source_scopes
from scripts.verify_current0054_hermetic import archive_files, archive_tree


def verify_archived_current54_capture(archive_path, identity, source_files, capture):
    archive_path = Path(archive_path)
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ValueError("current54_union_archive_missing")
    raw = archive_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != identity.get("archive_sha256"):
        raise ValueError("current54_union_archive_changed")
    with tempfile.TemporaryDirectory(prefix="ctcc-current54-union-") as directory:
        source = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            if (
                archive.pax_headers.get("comment") != identity.get("commit")
                or archive_tree(archive) != identity.get("tree")
                or archive_files(archive) != source_files
            ):
                raise ValueError("current54_union_archive_identity_changed")
            archive.extractall(source, filter="data")
        readback = current54.verify_current0054_owner_capture(capture, source)
        scopes = {
            "0052": source_scopes.historical0052_source_scope(source),
            "0053": source_scopes.historical0053_source_scope(source),
        }
        return readback, scopes
