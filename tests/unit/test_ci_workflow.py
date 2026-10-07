from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ctcc-v2-ci.yml"
)


def _branch_filters(workflow: str) -> dict[str, tuple[str, ...]]:
    pattern = re.compile(
        r"^  (?P<event>push|pull_request):\n"
        r"    branches:\n"
        r"(?P<branches>(?:      - .+\n)+)",
        flags=re.MULTILINE,
    )
    return {
        match.group("event"): tuple(
            line.removeprefix("      - ").strip().strip("'\"")
            for line in match.group("branches").splitlines()
        )
        for match in pattern.finditer(workflow)
    }


def test_hermetic_ci_covers_main_and_all_development_branches() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert _branch_filters(workflow) == {
        "push": ("main", "develop/**"),
        "pull_request": ("main", "develop/**"),
    }
    assert "develop/v1.6.8" not in workflow


def test_required_aggregate_rejects_failed_windows_regression() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    aggregate = jobs["regression"]

    assert set(aggregate["needs"]) == {
        "linux-postgres",
        "linux-shard",
        "windows-regression",
    }
    assert aggregate["if"] == "always()"
    guard = aggregate["steps"][0]["run"]
    for job in aggregate["needs"]:
        assert f"test '${{{{ needs.{job}.result }}}}' = success" in guard


def test_image_includes_core_blueprint_acceptance_inputs_explicitly() -> None:
    root = WORKFLOW.parents[2]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    copy_lines = {
        line.strip() for line in dockerfile.splitlines() if line.startswith("COPY ")
    }
    assert (
        "COPY README.md Dockerfile MANIFEST.sha256 compose.yaml .env.example .gitignore .dockerignore .gitattributes ./"
        in copy_lines
    )
    assert "COPY config ./config" in copy_lines
    # Keep build inputs allowlisted: no real configuration or workspace copy.
    assert "COPY . ." not in copy_lines
    assert "COPY .env " not in dockerfile
    ignored = (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".env*" in ignored and "!.env.example" in ignored
    for relative in ("README.md", "Dockerfile", "config/ctcc_core_blueprint.json"):
        assert (root / relative).is_file()
