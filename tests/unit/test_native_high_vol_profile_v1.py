"""Real compiled canonical guards; no source owner, SQL or venue is fabricated."""

import pytest

from app.trade_qualification import high_vol_momentum_qualification_v1 as profile
from app.trade_qualification import native_controlled_account_source_v6 as source
from app.trade_qualification import native_original_local_current55_v2 as original
from app.trade_qualification import native_postpublication_recheck_v2 as current
from app.trade_qualification.native_continuation_guard_v1 import (
    NativeSourceClosureError,
)


@pytest.mark.parametrize(
    "boundary",
    (source, original, current),
    ids=("flat-preparation", "original", "post-G12"),
)
def test_genuine_native_guards_include_original_high_vol_source(boundary):
    boundary._NATIVE_GUARD()


@pytest.mark.parametrize(
    "boundary",
    (source, original, current),
    ids=("flat-preparation", "original", "post-G12"),
)
def test_changed_high_vol_helper_cannot_pass_any_native_boundary(monkeypatch, boundary):
    boundary._NATIVE_GUARD()
    with monkeypatch.context() as patch:
        patch.setattr(
            profile,
            "evaluate_high_vol_momentum_permission_v1",
            lambda *_args, **_kw: True,
        )
        with pytest.raises(NativeSourceClosureError):
            boundary._NATIVE_GUARD()
    boundary._NATIVE_GUARD()


@pytest.mark.parametrize(
    "boundary",
    (source, original, current),
    ids=("flat-preparation", "original", "post-G12"),
)
def test_mutated_profile_policy_is_not_adopted_by_native_replay(monkeypatch, boundary):
    with monkeypatch.context() as patch:
        patch.setattr(profile, "PROFILE_SHA256", "f" * 64)
        with pytest.raises(NativeSourceClosureError):
            boundary._NATIVE_GUARD()


def test_caller_profile_setting_does_not_construct_original_or_current_owner():
    from app.trade_qualification.native_original_local_current55_v2 import (
        OwnedNativeOriginalLocalV2,
    )
    from app.trade_qualification.native_postpublication_recheck_v2 import (
        OwnedNativePostpublicationR7V2,
    )

    for cls in (OwnedNativeOriginalLocalV2, OwnedNativePostpublicationR7V2):
        with pytest.raises(ValueError):
            cls()
