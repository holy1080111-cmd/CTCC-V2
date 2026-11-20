"""Private exact-source Windows profile; an inert configuration, never a permit."""

from __future__ import annotations

import os
from pathlib import Path

from scripts import windows_tool_0058_v1 as package

ENVIRONMENT_NAME = "CTCC_WINDOWS_TOOL_CONFIG_FILE"
SCHEMA = "ctcc.windows_tool.physical0058.private_profile.v1"
_COMMON = frozenset({"api_token"})
_DEMO = frozenset(
    {
        "database_url",
        "ctcc_native_pg_tls_ca_file",
        "ctcc_native_pg_tls_ca_sha256",
        "okx_demo_api_key",
        "okx_demo_api_secret",
        "okx_demo_api_passphrase",
        "okx_demo_native_custody_database_url",
        "okx_demo_native_frame_reader_database_url",
        "okx_demo_native_evidence_root",
        "okx_demo_allowed_symbols",
        "okx_demo_scan_symbols",
        "ctcc_demo_high_vol_momentum_v2",
        "submission_outbox_enabled",
        "submission_outbox_root",
        "notion_outbox_enabled",
        "notion_outbox_root",
        "notion_outbox_destination_file",
        "notion_outbox_destination_sha256",
        "notion_outbox_token_file",
    }
)
_LIVE = frozenset(
    {
        "okx_live_api_key",
        "okx_live_api_secret",
        "okx_live_api_passphrase",
        "okx_live_expected_uid",
        "okx_live_expected_main_uid",
        "okx_live_rest_base_url",
    }
)


def _private(path):
    from app.trade_evidence.native_private_root_v2 import (
        _windows_private,
        require_private_evidence_base_v2,
    )

    package.need(path.is_absolute() and path.resolve(strict=True) == path)
    package.need(not path.is_relative_to(Path(__file__).resolve().parents[2]))
    require_private_evidence_base_v2(str(path.parent))
    if os.name == "nt":
        _windows_private(path)
    else:
        package.need(
            path.stat().st_uid == os.geteuid() and not path.stat().st_mode & 0o077
        )
    return package.regular(path, 65536)


def read_profile(path):
    """Read back the private file and all public source identity before settings."""
    raw = _private(path)
    document = package.json.loads(raw)
    package.need(
        type(document) is dict
        and set(document)
        == {
            "schema",
            "package_root",
            "package_identity_sha256",
            "mode",
            "port",
            "settings",
            "live_runtime",
        }
        and document["schema"] == SCHEMA
        and document["mode"] in ("analysis", "demo", "live")
        and type(document["port"]) is int
        and 1024 <= document["port"] <= 65535
        and type(document["settings"]) is dict
    )
    root = Path(document["package_root"])
    package.need(
        root.is_absolute() and root / "source" == Path(__file__).resolve().parents[2]
    )
    package.verify(root, document["package_identity_sha256"])
    allowed = (
        _COMMON
        | {"analysis": frozenset(), "demo": _DEMO, "live": _LIVE}[document["mode"]]
    )
    package.need(
        set(document["settings"]).issubset(allowed)
        and "api_token" in document["settings"]
    )
    package.need(
        type(document["settings"]["api_token"]) is str
        and len(document["settings"]["api_token"]) >= 32
    )
    runtime = document["live_runtime"]
    package.need(runtime is None or document["mode"] == "live")
    if runtime is not None:
        package.need(type(runtime) is dict and set(runtime) == {"path", "sha256"})
        package.need(package.sha(_private(Path(runtime["path"]))) == runtime["sha256"])
    package.need(_private(path) == raw)
    return document


def _values(settings_type, document):
    """Supply EVERY field at init priority, so no .env or ambient field wins."""
    values = {
        name: field.get_default(call_default_factory=True)
        for name, field in settings_type.model_fields.items()
    }
    package.need(
        all(not field.is_required() for field in settings_type.model_fields.values())
    )
    from app.core.native_only_lifespan import _MUST_BE_FALSE

    values.update(dict.fromkeys(_MUST_BE_FALSE, False))
    values.update(
        environment="production",
        host="127.0.0.1",
        port=document["port"],
        web_concurrency=1,
        log_level="CRITICAL",
        paper_auto_ticks=False,
        paper_persistence_enabled=False,
        paper_auto_execution=False,
        ctcc_native_only_lifespan_v1=False,
        ctcc_native_demo_manual_lifespan_0056_v1=False,
        ctcc_local_analysis_only_v1=False,
        trading_mode="analysis_only",
        okx_public_max_retries=0,
        okx_demo_read_max_retries=0,
        okx_live_read_max_retries=0,
        readiness_require_database=False,
        readiness_require_redis=False,
    )
    if document["mode"] == "demo":
        values.update(
            trading_mode="okx_demo",
            ctcc_native_demo_manual_lifespan_0056_v1=True,
            okx_demo_enabled=True,
            okx_demo_allow_order_writes=True,
            okx_demo_auto_execution=True,
            okx_ws_enabled=True,
            okx_ws_public_url="wss://wspap.okx.com:8443/ws/v5/public",
            okx_demo_max_leverage=20,
        )
    if document["mode"] == "live" and document["live_runtime"] is not None:
        values.update(trading_mode="live", okx_live_enabled=True)
    values.update(document["settings"])
    package.need(set(values) == set(settings_type.model_fields))
    return values


def load_settings(settings_type, filename):
    try:
        document = read_profile(Path(filename))
        result = settings_type(_env_file=None, **_values(settings_type, document))
        if document["mode"] == "demo":
            from app.core.native_manual_demo_lifespan_0056_v1 import (
                require_manual_demo_settings_0056_v1,
            )

            require_manual_demo_settings_0056_v1(result)
            package.need(
                result.okx_demo_credentials_configured
                and result.ctcc_native_pg_tls_ca_sha256 != ""
            )
        return result
    except Exception:  # noqa: BLE001 -- a bad private field may contain a secret.
        raise package.WindowsToolRefused("windows_tool_0058_profile_refused") from None
