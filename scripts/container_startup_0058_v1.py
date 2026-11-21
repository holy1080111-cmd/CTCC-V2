"""Current container entry point; never migrate a persistent database at boot.

Persistent Paper is a supported target, but its complete source58 catalog/actor
admission is not yet established. Keep that boundary closed before importing
the application, database, provider, or service recovery graph.
"""

from __future__ import annotations

import json
import os

_FALSE = (
    "CTCC_NATIVE_ONLY_LIFESPAN_V1",
    "CTCC_NATIVE_DEMO_MANUAL_LIFESPAN_0056_V1",
    "AUTO_TRADE",
    "LIVE_TRADING",
    "OKX_WS_ENABLED",
    "PAPER_AUTO_TICKS",
    "PAPER_AUTO_EXECUTION",
    "OKX_DEMO_ENABLED",
    "OKX_DEMO_ALLOW_ORDER_WRITES",
    "OKX_DEMO_AUTO_EXECUTION",
    "OKX_DEMO_AUTO_RECONCILE_ON_START",
    "OKX_DEMO_OBSERVABILITY_ENABLED",
    "OKX_DEMO_SOAK_ENABLED",
    "OKX_DEMO_SOAK_ALLOW_EXECUTE",
    "OKX_DEMO_CONTINUOUS_SESSION_ENABLED",
    "OKX_LIVE_ENABLED",
    "OKX_LIVE_ALLOW_ORDER_WRITES",
    "OKX_LIVE_AUTO_EXECUTION",
    "OKX_LIVE_AUTO_RECONCILE_ON_START",
    "NOTION_OUTBOX_ENABLED",
    "SUBMISSION_OUTBOX_ENABLED",
)
_EXCHANGE_SECRETS = (
    "OKX_DEMO_API_KEY",
    "OKX_DEMO_API_SECRET",
    "OKX_DEMO_API_PASSPHRASE",
    "OKX_LIVE_API_KEY",
    "OKX_LIVE_API_SECRET",
    "OKX_LIVE_API_PASSPHRASE",
)


class ContainerStartupRefused(RuntimeError):
    """Fixed refusal codes contain no operator configuration values."""


def _require_closed_mode() -> None:
    mode = os.environ.get("CTCC_CONTAINER_MODE_V1")
    if mode is None or mode == "":
        raise ContainerStartupRefused("container_mode_required")
    if mode != "persistent_paper":
        raise ContainerStartupRefused("container_mode_unsupported")
    required = {
        "ENVIRONMENT": "production",
        "TRADING_MODE": "analysis_only",
        "WEB_CONCURRENCY": "1",
        "CTCC_LOCAL_ANALYSIS_ONLY_V1": "true",
        "PAPER_PERSISTENCE_ENABLED": "true",
    }
    if (
        any(os.environ.get(name) != value for name, value in required.items())
        or any(os.environ.get(name) != "false" for name in _FALSE)
        or any(os.environ.get(name, "") != "" for name in _EXCHANGE_SECRETS)
    ):
        raise ContainerStartupRefused("container_configuration_refused")


def main() -> int:
    try:
        _require_closed_mode()
        # A mode name, exact head, or caller receipt is not catalog admission.
        # The later source-owned Paper preflight must precede all app imports
        # and invoke the existing Paper recovery context, without auto-DDL.
        raise ContainerStartupRefused("container_paper_source_admission_pending")
    except ContainerStartupRefused as error:
        print(json.dumps({"status": "REFUSED", "code": str(error)}, sort_keys=True))
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
