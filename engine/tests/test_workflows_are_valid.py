"""A workflow that does not parse is a workflow that silently stops existing.

Editing `db-backup.yml` by script, a continuation line was indented less than
the `run: |` block scalar containing it. YAML ended the block there and parsed
the remainder of the file as mapping keys, so the document still *looked*
fine, still committed, still pushed — and GitHub answered a dispatch with
"Workflow does not have 'workflow_dispatch' trigger", because as far as it was
concerned the triggers were gone.

Nothing on GitHub tells you this. There is no failed run to look at: a
workflow that cannot be parsed does not run, and a job that never runs leaves
no red tick. It is the same shape as the bug these files exist to prevent —
the thing that failed looks exactly like the thing that had nothing to do.

This is also not hypothetical for this repository specifically. The backup
workflow is the only automated protection the corpus has, and the corpus is
the one part of this system that cannot be rebuilt from this repository.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = sorted((Path(__file__).resolve().parents[2] / ".github" / "workflows")
                   .glob("*.yml"))


def test_there_are_workflows_to_check():
    """Guards the guard: a glob that matches nothing passes every test below
    while protecting nothing."""
    assert WORKFLOWS, "no workflows found — has the directory moved?"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_the_workflow_parses(path):
    loaded = yaml.safe_load(path.read_text())
    assert isinstance(loaded, dict), f"{path.name} is not a YAML mapping"
    assert loaded.get("jobs"), f"{path.name} declares no jobs"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_the_workflow_keeps_its_triggers(path):
    """`on:` is the one key YAML will quietly mangle: unquoted, it parses as
    the boolean True, and a file whose triggers have been swallowed by a
    broken block scalar loses it entirely."""
    loaded = yaml.safe_load(path.read_text())
    triggers = loaded.get("on", loaded.get(True))
    assert triggers, f"{path.name} has no triggers — it can never run"


def test_the_backup_can_still_be_run_by_hand():
    """The whole rescue procedure in docs/DEPLOY_FLY.md is "go to Actions and
    press Run workflow". That button exists only if `workflow_dispatch` is
    declared, and it had silently stopped being."""
    path = next(p for p in WORKFLOWS if p.name == "db-backup.yml")
    loaded = yaml.safe_load(path.read_text())
    triggers = loaded.get("on", loaded.get(True))
    assert "workflow_dispatch" in triggers, (
        "db-backup can no longer be triggered by hand — the Run workflow "
        "button will not appear")
    assert "schedule" in triggers, "db-backup no longer runs nightly"


# --- the deploy config is as load-bearing as the workflows --------------------

def test_the_railway_config_points_at_a_dockerfile_that_exists():
    """Railway looks for a `Dockerfile` at the repository root by default and
    this project keeps its one under engine/. If `railway.json` stops parsing,
    or the path drifts, Railway does not complain — it falls back, finds
    nothing, and the build fails with a message about a missing Dockerfile
    that is nowhere near the actual cause.
    """
    import json

    root = Path(__file__).resolve().parents[2]
    cfg = json.loads((root / "railway.json").read_text())
    path = cfg["build"]["dockerfilePath"]
    assert (root / path).exists(), f"railway.json points at {path}, which is not there"


def test_the_app_is_not_allowed_to_sleep():
    """A report runs for tens of minutes as a background job, so it generates
    no web traffic while it works. Any idle-sleep feature watches web traffic
    only, and would suspend the machine mid-run — leaving the page on a report
    that never finishes, which is the exact failure this codebase spent a week
    removing from the pipeline.
    """
    import json

    root = Path(__file__).resolve().parents[2]
    cfg = json.loads((root / "railway.json").read_text())
    assert cfg["deploy"]["sleepApplication"] is False
