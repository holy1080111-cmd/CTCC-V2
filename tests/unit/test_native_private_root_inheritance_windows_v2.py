"""Windows native evidence children inherit only a verified private parent ACL.

The DACL changes below are confined to pytest-owned temporary directories.
No database, exchange client, credential, or order route is used.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.demo_automation import native_caller_v2, native_source_only_v1
from app.demo_automation.native_private_root_v2 import (
    NativeEvidenceRootErrorV2,
    mkdir_private_evidence_child_v2,
    require_private_evidence_base_v2,
)
from app.trade_qualification import account_native_proof_storage as storage
from tests.native_private_temp_v1 import (
    _owner_sid_string as _owner_sid_string,  # noqa: PLC0414 -- compatibility re-export.
)
from tests.native_private_temp_v1 import (
    _set_protected_directory_dacl as _set_protected_directory_dacl,  # noqa: PLC0414 -- compatibility re-export.
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows DACL contract")


def _protected_base(tmp_path: Path) -> Path:
    base = tmp_path / "native-private"
    base.mkdir()
    _set_protected_directory_dacl(base, initialize_owner=True)
    assert require_private_evidence_base_v2(str(base)) == base
    return base


def test_source_only_children_inherit_verified_private_acl(tmp_path):
    base = _protected_base(tmp_path)
    roots = native_source_only_v1._roots(
        SimpleNamespace(okx_demo_native_evidence_root=str(base))
    )
    assert set(roots) == set(native_source_only_v1._ROOT_NAMES)
    attempt = next(iter(roots.values())).parent
    assert attempt.parent == base
    assert require_private_evidence_base_v2(str(attempt)) == attempt
    assert {path.parent for path in roots.values()} == {attempt}
    assert len({storage._root_identity(path) for path in roots.values()}) == len(roots)
    for path in roots.values():
        assert require_private_evidence_base_v2(str(path)) == path
        with storage._root_context(path) as directory:
            assert not directory.names()


def test_native_caller_children_inherit_verified_private_acl_and_no_clobber(tmp_path):
    base = _protected_base(tmp_path)
    invocation = "n121-" + uuid.uuid4().hex
    roots = native_caller_v2._roots(
        SimpleNamespace(okx_demo_native_evidence_root=str(base)), invocation
    )
    assert set(roots) == set(native_caller_v2._ROOT_NAMES)
    attempt = base / invocation
    assert require_private_evidence_base_v2(str(attempt)) == attempt
    for path in roots.values():
        assert path.parent == attempt
        assert require_private_evidence_base_v2(str(path)) == path
        with storage._root_context(path) as directory:
            assert not directory.names()
    with pytest.raises(OSError):
        native_caller_v2._roots(
            SimpleNamespace(okx_demo_native_evidence_root=str(base)), invocation
        )
    assert require_private_evidence_base_v2(str(attempt)) == attempt


def test_native_helper_rejects_untrusted_parent_before_child_creation(tmp_path):
    base = _protected_base(tmp_path)
    _set_protected_directory_dacl(base, extra_users=True)
    with (
        storage._root_context(base) as directory,
        pytest.raises(NativeEvidenceRootErrorV2),
    ):
        mkdir_private_evidence_child_v2(directory, "never-created")
    assert not (base / "never-created").exists()


def test_native_validator_rejects_new_unauthorized_child_ace(tmp_path):
    base = _protected_base(tmp_path)
    with storage._root_context(base) as directory:
        mkdir_private_evidence_child_v2(directory, "verified-child")
    child = base / "verified-child"
    assert require_private_evidence_base_v2(str(child)) == child
    _set_protected_directory_dacl(child, extra_users=True)
    with pytest.raises(NativeEvidenceRootErrorV2):
        require_private_evidence_base_v2(str(child))
