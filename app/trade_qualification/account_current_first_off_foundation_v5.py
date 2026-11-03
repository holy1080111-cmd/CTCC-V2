"""Versioned OFF source acquisition; never an armed or submission capability."""

import asyncio

_SOURCE_KEY = object()
_UNKNOWN_RELEASES = []
MAX_BINDINGS = 16


def _assembly():
    from app.trade_qualification import native_account_risk_assembly_v1

    return native_account_risk_assembly_v1


async def acquire_current_first_off_foundation_v5(
    *,
    read_session,
    private_client,
    scope_config,
    current_account_plan,
    session_factory,
    custody_session_factory,
    frame_session_factory,
    frame_collector_role_oid,
    history_raw_root,
    history_companion_root,
    account_proof_root,
    account_native_root,
):
    owner = await _assembly().acquire_native_account_history_foundation_v5(
        read_session=read_session,
        private_client=private_client,
        scope_config=scope_config,
        current_account_plan=current_account_plan,
        session_factory=session_factory,
        custody_session_factory=custody_session_factory,
        frame_session_factory=frame_session_factory,
        frame_collector_role_oid=frame_collector_role_oid,
        history_raw_root=history_raw_root,
        history_companion_root=history_companion_root,
        account_proof_root=account_proof_root,
        account_native_root=account_native_root,
    )
    return await _assembly()._continue_current_first_api_asof_v3(owner, key=_SOURCE_KEY)


def release_current_first_off_foundation_v5(owner):
    try:
        _assembly()._release_current_first_owner_v5(owner, key=_SOURCE_KEY)
    except asyncio.CancelledError:
        raise asyncio.CancelledError() from None
    except (KeyboardInterrupt, SystemExit) as error:
        raise type(error)() from None
    except Exception:  # noqa: BLE001 -- no private account/source/release values.
        raise ValueError("current_first_off_v5_release_unknown") from None
