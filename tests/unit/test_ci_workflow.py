from __future__ import annotations

from pathlib import Path
import re


WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "ctcc-v2-ci.yml"
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
