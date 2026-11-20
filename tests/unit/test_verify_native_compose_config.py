"""Synthetic effective-config admission and refusal cases."""

import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import verify_native_compose_config as verifier


def _base_merge():
    image = "ctcc-native-reviewed:exact-source"
    ident = "abcdef123456"
    base = {
        "networks": {"native_internal": None},
        "ports": [],
        "restart": "no",
        "profiles": ["historical55-owner-setup"],
    }
    api = {
        **base,
        "image": image,
        "pull_policy": "never",
        "entrypoint": ["/bin/sh", "-ec"],
        "command": [verifier._RUNTIME_SCRIPT.replace("$", "$$")],
        "container_name": f"ctcc-native-{ident}-api",
        "profiles": ["historical55-api-forbidden"],
        "secrets": [],
        "environment": dict(verifier._RUNTIME_ENV),
        "healthcheck": dict(verifier._RUNTIME_HEALTHCHECK),
        "security_opt": ["no-new-privileges:true"],
    }
    document = {
        "name": f"ctcc-native-{ident}",
        "networks": {"native_internal": {"internal": True}},
        "services": {
            "api": api,
            "native_migrate": {
                **base,
                "image": image,
                "pull_policy": "never",
                "entrypoint": ["/bin/sh", "-ec"],
                "command": [verifier._MIGRATE_SCRIPT],
                "environment": dict(verifier._OWNER_ENV),
                "secrets": ["native_owner_dsn"],
                "depends_on": {"postgres": {"condition": "service_healthy"}},
            },
            "native_role_provision": {
                **base,
                "image": image,
                "pull_policy": "never",
                "entrypoint": [
                    "python",
                    "-B",
                    "-m",
                    "scripts.provision_native_roles_v1",
                ],
                "command": ["--provision-reviewed-native-roles"],
                "environment": dict(verifier._OWNER_ENV),
                "profiles": ["historical55-owner-setup"],
                "depends_on": {
                    "native_migrate": {"condition": "service_completed_successfully"}
                },
                "secrets": [
                    "native_owner_dsn",
                    "native_writer_dsn",
                    "native_custody_dsn",
                    "native_frame_dsn",
                ],
            },
            "postgres": {
                **base,
                "container_name": f"ctcc-native-{ident}-postgres",
                "environment": {
                    "POSTGRES_PASSWORD_FILE": "/run/secrets/native_postgres_init_password"
                },
                "secrets": ["native_postgres_init_password"],
                "healthcheck": {
                    "test": [
                        "CMD-SHELL",
                        verifier._POSTGRES_HEALTHCHECK_SCRIPT.replace("$", "$$"),
                    ]
                },
            },
            "redis": {
                **base,
                "container_name": f"ctcc-native-{ident}-redis",
            },
        },
    }
    return document, ident, image


def test_exact_no_execution_merge():
    document, ident, image = _base_merge()
    assert verifier.verify(document, ident, image)["status"] == "PASS"


def test_exact_postgres_healthcheck_without_config_dollar_escape():
    document, ident, image = _base_merge()
    document["services"]["postgres"]["healthcheck"]["test"] = [
        "CMD-SHELL",
        verifier._POSTGRES_HEALTHCHECK_SCRIPT,
    ]
    assert verifier.verify(document, ident, image)["status"] == "PASS"


def test_effective_compose_dependency_defaults_keep_required_completed_grant_order():
    document, ident, image = _base_merge()
    for service, dependency in (
        ("native_migrate", "postgres"),
        ("native_role_provision", "native_migrate"),
    ):
        document["services"][service]["depends_on"][dependency].update(
            required=True, restart=False
        )
    result = verifier.verify(document, ident, image)
    assert result["database_head"] == "0055" and result["final0058_runtime"] is False


@pytest.mark.parametrize(
    "change",
    [
        "head",
        "0058",
        "early_provision",
        "optional_migration",
        "automatic_restart",
        "api_dependency",
    ],
)
def test_historical55_cannot_admit_current_head_or_early_grants_or_api_start(change):
    document, ident, image = _base_merge()
    if change in {"head", "0058"}:
        document["services"]["native_migrate"]["command"] = [
            verifier._MIGRATE_SCRIPT.replace("0055", change)
        ]
    elif change == "early_provision":
        document["services"]["native_role_provision"]["depends_on"] = {
            "postgres": {"condition": "service_healthy"}
        }
    elif change == "optional_migration":
        document["services"]["native_role_provision"]["depends_on"]["native_migrate"][
            "required"
        ] = False
    elif change == "automatic_restart":
        document["services"]["postgres"]["restart"] = "unless-stopped"
    else:
        document["services"]["api"]["depends_on"] = {
            "native_migrate": {"condition": "service_completed_successfully"}
        }
    with pytest.raises(ValueError, match="native_compose_merge_denied"):
        verifier.verify(document, ident, image)


def test_actual_source_override_stops_at55_and_cannot_launch_intermediate_api():
    class SourceLoader(yaml.SafeLoader):
        pass

    def compose_tag(loader, node):
        if isinstance(node, yaml.MappingNode):
            return loader.construct_mapping(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        return None

    SourceLoader.add_constructor("!reset", compose_tag)
    SourceLoader.add_constructor("!override", compose_tag)
    root = Path(__file__).resolve().parents[2]
    config = yaml.load(
        (root / "config/compose.native-only.override.yaml").read_text(),
        Loader=SourceLoader,
    )
    services = config["services"]
    assert services["native_migrate"]["command"] == [
        verifier._MIGRATE_SCRIPT.replace("$", "$$")
    ]
    assert services["native_role_provision"]["depends_on"] == {
        "native_migrate": {"condition": "service_completed_successfully"}
    }
    for name, service in services.items():
        assert service["restart"] == "no"
        assert service["profiles"] == [
            "historical55-api-forbidden"
            if name == "api"
            else "historical55-owner-setup"
        ]
    api = services["api"]
    assert (
        api["secrets"] == []
        and api["depends_on"] == {}
        and api["healthcheck"] == {"disable": True}
    )
    argv = shlex.split(api["command"][0])
    assert argv[:3] == ["exec", "python", "-c"] and len(argv) == 4
    # Execute only the exact reviewed refusal expression, not Compose/server IO.
    assert api["command"] == [verifier._RUNTIME_SCRIPT]
    result = subprocess.run(
        [sys.executable, "-I", "-c", argv[3]], check=False, capture_output=True
    )
    assert result.returncode != 0 and result.stdout == b""
    assert result.stderr.strip() == b"historical0055_bootstrap_api_forbidden"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        pytest.param(("services", "api", "build"), {"context": "."}, id="local-build"),
        pytest.param(("services", "api", "ports"), ["8100:8000"], id="published-port"),
        pytest.param(("services", "api", "env_file"), [".env"], id="host-env-file"),
        pytest.param(
            ("services", "api", "secrets"),
            ["native_writer_dsn", "native_owner_dsn"],
            id="owner-secret-in-api",
        ),
        pytest.param(
            ("services", "api", "networks"), {"default": None}, id="external-network"
        ),
        pytest.param(
            ("services", "api", "environment", "OKX_DEMO_ALLOW_ORDER_WRITES"),
            "true",
            id="order-writes-enabled",
        ),
        pytest.param(
            ("services", "postgres", "environment", "POSTGRES_PASSWORD"),
            "synthetic",
            id="inline-db-password",
        ),
        pytest.param(
            ("services", "postgres", "healthcheck", "test"),
            ["CMD-SHELL", "pg_isready -U ctcc -d ctcc"],
            id="postgres-unix-socket-bootstrap-healthcheck",
        ),
        pytest.param(
            ("services", "postgres", "healthcheck", "test"),
            ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U ctcc -d ctcc"],
            id="postgres-tcp-without-initialized-database-query",
        ),
        pytest.param(
            ("services", "postgres", "healthcheck", "test"),
            ["CMD-SHELL", verifier._POSTGRES_HEALTHCHECK_SCRIPT + " || true"],
            id="postgres-sql-refusal-suppressed",
        ),
        pytest.param(
            ("services", "api", "command"),
            ["printf injected\n" + verifier._RUNTIME_SCRIPT],
            id="modified-runtime-command",
        ),
        pytest.param(
            ("services", "native_migrate", "command"),
            ["export", "DATABASE_URL=$$(cat /run/secrets/native_owner_dsn)"],
            id="truncated-migration-command",
        ),
        pytest.param(
            ("services", "api", "volumes"),
            [
                {
                    "type": "bind",
                    "source": "/synthetic-host",
                    "target": "/synthetic-mount",
                }
            ],
            id="unexpected-bind-mount",
        ),
        pytest.param(
            ("services", "api", "environment", "NOTION_TOKEN"),
            "synthetic",
            id="unexpected-token-environment",
        ),
        pytest.param(
            ("services", "api", "healthcheck", "test"),
            ["CMD-SHELL", "true"],
            id="changed-runtime-healthcheck",
        ),
        pytest.param(
            ("services", "native_migrate", "secrets"),
            [{"source": "native_owner_dsn", "target": "different"}],
            id="changed-secret-target",
        ),
    ],
)
def test_inherited_or_broadened_api_refused(path, value):
    document, ident, image = _base_merge()
    cursor = document
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    with pytest.raises(ValueError, match="native_compose_merge_denied"):
        verifier.verify(document, ident, image)
