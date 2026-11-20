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


def test_required_aggregate_rejects_failed_windows_full_suite_union() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    aggregate = jobs["regression"]

    assert set(aggregate["needs"]) == {
        "source-profile",
        "linux-postgres",
        "linux-shard",
        "windows-union",
    }
    assert aggregate["if"] == "always()"
    assert aggregate["name"] == "Docker hermetic regression"
    guard = aggregate["steps"][0]["run"]
    for job in aggregate["needs"]:
        assert f"test '${{{{ needs.{job}.result }}}}' = success" in guard
    assert jobs["windows-union"]["needs"] == ["windows-shard"]
    assert jobs["windows-shard"]["strategy"]["matrix"]["shard"] == list(range(8))
    assert jobs["windows-shard"]["strategy"]["fail-fast"] is False


def test_postgres_topology_is_bound_to_verified_source_and_required_status():
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    assert jobs["linux-postgres"]["needs"] == ["source-profile"]
    assert (
        jobs["linux-postgres"]["strategy"]["matrix"]["component"]
        == "${{ fromJSON(needs.source-profile.outputs.postgres-components) }}"
    )
    source = jobs["source-profile"]
    proof = next(step["run"] for step in source["steps"] if step.get("id") == "profile")
    assert 'test "$commit" = "$GITHUB_SHA"' in proof
    assert "scripts.hermetic_workflow_profile_v1" in proof
    assert '--archive-sha256 "$digest"' in proof and '--tree "$tree"' in proof


def test_image_includes_core_blueprint_acceptance_inputs_explicitly() -> None:
    root = WORKFLOW.parents[2]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    copy_lines = {
        line.strip() for line in dockerfile.splitlines() if line.startswith("COPY ")
    }
    assert (
        "COPY README.md Dockerfile MANIFEST.sha256 compose.yaml .env.example .gitignore .dockerignore .gitattributes Open-CTCC.cmd ./"
        in copy_lines
    )
    assert "COPY config ./config" in copy_lines
    # Keep build inputs allowlisted: no real configuration or workspace copy.
    assert "COPY . ." not in copy_lines
    assert "COPY .env " not in dockerfile
    ignored = (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".env*" in ignored and "!.env.example" in ignored
    for relative in (
        "README.md",
        "Dockerfile",
        "Open-CTCC.cmd",
        "config/ctcc_core_blueprint.json",
    ):
        assert (root / relative).is_file()
