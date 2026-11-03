"""Redacted effective-Compose verifier; reads config JSON from stdin only.

This does not invoke Docker, open DSN files, or make an acceptance claim.
The caller must inspect actual containers/secret mounts separately.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

_ID = re.compile(r"[0-9a-f]{12}\Z")
_FLAGS = (
    "AUTO_TRADE",
    "LIVE_TRADING",
    "PAPER_PERSISTENCE_ENABLED",
    "PAPER_AUTO_TICKS",
    "PAPER_AUTO_EXECUTION",
    "OKX_WS_ENABLED",
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


def _need(value):
    if value is not True:
        raise ValueError("native_compose_merge_denied")


def _secrets(service):
    return {
        item if type(item) is str else item.get("source")
        for item in service.get("secrets", [])
    }


def verify(document, deployment_id, image_ref):
    _need(type(document) is dict and type(document.get("services")) is dict)
    _need(document.get("name") == f"ctcc-native-{deployment_id}")
    services = document["services"]
    _need(
        set(services)
        == {"api", "native_migrate", "native_role_provision", "postgres", "redis"}
    )
    network = document.get("networks", {}).get("native_internal", {})
    _need(network.get("internal") is True)
    for name in services:
        service = services[name]
        _need(set(service.get("networks", {})) == {"native_internal"})
        _need(not service.get("ports"))
    for name in ("api", "native_migrate", "native_role_provision"):
        service = services[name]
        _need(service.get("image") == image_ref)
        _need(service.get("pull_policy") == "never")
        _need(not service.get("build"))
    api = services["api"]
    _need(api.get("entrypoint") == ["/bin/sh", "-ec"])
    _need(not api.get("env_file"))
    _need(api.get("container_name") == f"ctcc-native-{deployment_id}-api")
    _need(api.get("profiles") == ["native-runtime"])
    _need(
        _secrets(api)
        == {
            "native_writer_dsn",
            "native_custody_dsn",
            "native_frame_dsn",
            "native_api_token",
        }
    )
    _need(_secrets(services["native_migrate"]) == {"native_owner_dsn"})
    _need(not services["native_migrate"].get("env_file"))
    _need(services["native_role_provision"].get("profiles") == ["native-role-setup"])
    _need(not services["native_role_provision"].get("env_file"))
    _need(
        _secrets(services["native_role_provision"])
        == {
            "native_owner_dsn",
            "native_writer_dsn",
            "native_custody_dsn",
            "native_frame_dsn",
        }
    )
    for name in ("postgres", "redis"):
        _need(
            services[name].get("container_name")
            == f"ctcc-native-{deployment_id}-{name}"
        )
    postgres_env = services["postgres"].get("environment", {})
    _need("POSTGRES_PASSWORD" not in postgres_env)
    _need(
        postgres_env.get("POSTGRES_PASSWORD_FILE")
        == "/run/secrets/native_postgres_init_password"
    )
    _need(_secrets(services["postgres"]) == {"native_postgres_init_password"})
    environment = api.get("environment", {})
    _need(type(environment) is dict)
    _need(environment.get("ENVIRONMENT") == "production")
    _need(environment.get("TRADING_MODE") == "okx_demo")
    _need(environment.get("CTCC_NATIVE_ONLY_LIFESPAN_V1") == "true")
    _need(environment.get("WEB_CONCURRENCY") == "1")
    _need(environment.get("OKX_DEMO_ENABLED") == "true")
    _need(all(environment.get(key) == "false" for key in _FLAGS))
    _need(
        not any(
            key in environment
            for key in (
                "DATABASE_URL",
                "POSTGRES_PASSWORD",
                "OKX_DEMO_API_KEY",
                "OKX_DEMO_API_SECRET",
                "OKX_DEMO_API_PASSPHRASE",
                "OKX_LIVE_API_KEY",
                "OKX_LIVE_API_SECRET",
                "OKX_LIVE_API_PASSPHRASE",
            )
        )
    )
    command = api.get("command")
    if type(command) is list:
        command = " ".join(command)
    _need(type(command) is str and "exec uvicorn app.main:app" in command)
    _need("alembic" not in command and "native_owner_dsn" not in command)
    return {
        "status": "PASS",
        "profile": "native_no_execution_merge_v1",
        "execution_authority": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--image-ref", required=True)
    args = parser.parse_args()
    if _ID.fullmatch(args.deployment_id) is None or not args.image_ref:
        print('{"status":"FAIL","code":"native_compose_input_invalid"}')
        return 1
    try:
        raw = sys.stdin.buffer.read(2_000_001)
        _need(0 < len(raw) <= 2_000_000)
        result = verify(json.loads(raw), args.deployment_id, args.image_ref)
    except Exception:  # noqa: BLE001 - malformed merged input emits a fixed refusal
        print('{"status":"FAIL","code":"native_compose_merge_denied"}')
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
