from __future__ import annotations

import re
from pathlib import Path

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


def test_image_includes_core_blueprint_acceptance_inputs_explicitly() -> None:
    root = WORKFLOW.parents[2]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    copy_lines = {
        line.strip() for line in dockerfile.splitlines() if line.startswith("COPY ")
    }
    assert "COPY README.md Dockerfile ./" in copy_lines
    assert (
        "COPY config/ctcc_core_blueprint.json ./config/ctcc_core_blueprint.json"
        in copy_lines
    )
    # Keep build inputs allowlisted: no real configuration or workspace copy.
    assert "COPY config ./config" not in copy_lines
    assert "COPY . ." not in copy_lines
    for relative in ("README.md", "Dockerfile", "config/ctcc_core_blueprint.json"):
        assert (root / relative).is_file()
