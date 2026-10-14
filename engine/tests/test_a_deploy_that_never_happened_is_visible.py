"""Twenty-three commits that were pushed and never ran.

Railway deployed this service by watching the repository through its GitHub
App. On 9 October 2026 that connection broke. The service's Settings ->
Source still named the right repository and the right branch — the operator
checked, twice, and was right both times — and two lines below it said
"Auto deploy unavailable" and, in red, "Could not load branches. Retry".

Railway had stopped deploying and said so only there. The site stayed up.
It served 9 October's code for three days and looked entirely live, while
twenty-three commits landed on the branch and not one of them ran.

The cost was not the stale code. It was that every fix pushed in that window
was tested against a container which did not contain it, so each appeared
not to work, and the next diagnosis started from a false fact. One of those
"fixes that did not work" was the connection-release fix in
`test_no_connection_is_held_across_a_long_phase.py`, which was correct the
first time.

This is this project's founding rule at the scale of the whole deployment:
**a deploy that never happened must never look like a deploy that
happened.** It is pinned here rather than left to the docs because the
docs did not prevent it and a test is the only thing in this repository that
argues back.

Three things have to hold, and none of them is "Railway works":

  1. The repository can deploy itself, so losing the host's view of GitHub
     cannot stop a release (`deploy.yml`).
  2. A deploy proves itself against the running container rather than
     trusting the deploying tool's exit code.
  3. Something notices when NEITHER path ran (`deploy-drift.yml`) — the
     case a deploy workflow cannot catch, because it was never triggered.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
DEPLOY = WORKFLOWS / "deploy.yml"
DRIFT = WORKFLOWS / "deploy-drift.yml"
IMAGE = WORKFLOWS / "image.yml"
DOCKERFILE = (ROOT / "engine" / "Dockerfile.render").read_text()


def _yaml(path: Path) -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(path.read_text())


def _triggers(spec: dict) -> dict:
    # PyYAML resolves a bare `on:` key to the boolean True, which is why
    # every naive reader of a GitHub workflow finds no triggers at all.
    return spec.get("on") or spec.get(True) or {}


def test_the_repository_can_deploy_itself():
    """Without this the only route to production is the host choosing to
    pull, which is a single point of failure nothing here can see or fix."""
    assert DEPLOY.exists(), (
        "there is no deploy workflow — the only way code reaches production "
        "is the host deciding to pull it, which is what failed for three days")
    spec = _yaml(DEPLOY)
    assert "push" in _triggers(spec), "the deploy does not run on a push"


def test_the_deploy_watches_the_branch_this_service_deploys_from():
    """A deploy workflow pointed at the wrong branch is the same silent
    nothing it exists to prevent, so the branch is asserted, not assumed."""
    branches = _triggers(_yaml(DEPLOY))["push"]["branches"]
    assert "claude/magical-mayer-yzxz5s" in branches, (
        "the live deploy branch is not in the push trigger — pushes to it "
        f"will deploy nothing. Watching: {branches}")


def test_a_deploy_proves_itself_against_the_running_container():
    """`railway up` exiting 0 means the upload landed. It does not mean the
    new code is serving — a container can build and then crash-loop, and the
    host rolls back to the last one that started."""
    body = DEPLOY.read_text()
    assert "/api/health" in body, "the deploy never asks the live site anything"
    assert "build_detail" in body, (
        "the deploy does not read build_detail, so it cannot tell which "
        "commit is actually running")
    assert "GITHUB_SHA" in body
    # It must FAIL on a mismatch. A check that reports a mismatch and exits
    # 0 is a green tick on a deploy that did not happen, which is the whole
    # defect wearing a different hat.
    assert "exit 1" in body.split("Verify the live site")[1], (
        "the verification step cannot fail — a mismatch would be reported "
        "into a log and the run would go green")


def test_something_notices_when_no_deploy_ran_at_all():
    """The case `deploy.yml` structurally cannot catch: it was never
    triggered, or its secret expired, so there is no run to go red."""
    assert DRIFT.exists(), (
        "nothing compares the live commit against the branch on a schedule, "
        "so a deploy that simply never happened is invisible again")
    spec = _yaml(DRIFT)
    assert "schedule" in _triggers(spec), (
        "the drift check only runs when someone asks it to, which is the "
        "same as not having it")
    body = DRIFT.read_text()
    assert "issue create" in body and "issue close" in body, (
        "the drift check does not raise an alarm anywhere that gets read — "
        "a red cross in a tab nobody opens is not a signal")


def test_the_drift_alarm_does_not_cry_wolf_over_documentation():
    """`deploy.yml` skips docs-only commits, so a docs commit at the tip is
    not drift. An alarm that fires on correct state is one its reader learns
    to skip — the same conclusion `style_check.py` reached."""
    deploy = DEPLOY.read_text()
    assert "paths-ignore" in deploy
    drift = DRIFT.read_text()
    assert "':!docs'" in drift and "':!*.md'" in drift, (
        "the drift check compares against plain HEAD, so every documentation "
        "commit will be reported as a failed deploy")


def test_a_pushed_deploy_can_still_identify_its_commit():
    """`railway up` uploads a tarball, so the host sets none of its
    RAILWAY_GIT_* variables and has no commit to bake in. Without the file
    fallback the container reports a build with no identity — and an
    unidentifiable deploy makes every diagnosis after it a guess, which is
    exactly the state these three days were spent in."""
    assert "BUILD_COMMIT" in DOCKERFILE, (
        "the image has no way to learn its commit when the host cannot pass "
        "one, so /api/health would report a build with no commit")
    assert "BUILD_COMMIT" in DEPLOY.read_text(), (
        "nothing writes the commit into the tree that gets uploaded")
    stamp = DOCKERFILE.split("ARG GIT_COMMIT")[1]
    assert "GIT_COMMIT" in stamp and "BUILD_COMMIT" in stamp, (
        "the host-provided commit must still win over the file")
    assert "engine/BUILD_COMMIT" in (ROOT / ".gitignore").read_text(), (
        "a committed BUILD_COMMIT is stale by exactly one commit, every time")


def test_the_drift_answer_is_readable_without_opening_a_log():
    """GitHub serves raw workflow logs from blob storage, so a fact printed
    with `echo` is reachable from a browser and nowhere else — not from the
    API, not from a CLI, not from another agent. `db-backup.yml` learned
    that when the reason a month of backups failed turned out to be
    unreadable from anywhere except a browser tab nobody opened.

    This job exists to produce exactly one fact. It has to survive into the
    run's annotations and its summary, whatever the verdict."""
    body = DRIFT.read_text()
    compare = body.split("Compare the branch head")[1].split("- name:")[0]
    assert "::notice::live=" in compare, (
        "the live commit is only echoed into the log body, so the one fact "
        "this job produces cannot be read from the API")
    assert "GITHUB_STEP_SUMMARY" in compare, (
        "the comparison does not reach the run summary")


def test_there_is_a_route_to_production_that_needs_no_credential():
    """Both other routes depend on something outside this repository: the
    host's GitHub App (which broke) or a RAILWAY_TOKEN somebody has to
    create and paste (which is a step that can be got wrong, and was).

    `GITHUB_TOKEN` is issued to the job automatically. A route built on it
    alone cannot be blocked by a dashboard, so the image is always
    available even when no host will deploy it — and the image is the thing
    worth not losing.
    """
    assert IMAGE.exists(), (
        "there is no credential-free way to produce a runnable artifact, so "
        "every path to production depends on an integration or a secret")
    spec = _yaml(IMAGE)
    assert "push" in _triggers(spec)
    body = IMAGE.read_text()
    assert "secrets.GITHUB_TOKEN" in body
    assert "packages: write" in body, (
        "the job cannot publish, so it will fail at the registry")
    # Any other secret would reintroduce the dependency this route removes.
    others = [line for line in body.splitlines()
              if "secrets." in line and "GITHUB_TOKEN" not in line]
    assert not others, (
        f"the image route now needs a secret of its own: {others}")


def test_the_published_image_can_say_which_commit_it_is():
    """An image that cannot identify itself puts the deployment back in the
    state where every diagnosis is a guess — and building off-host is
    precisely when the host can tell you nothing."""
    body = IMAGE.read_text()
    assert "GIT_COMMIT=${{ github.sha }}" in body, (
        "the build does not pass the commit, so /api/health would report an "
        "unidentifiable build and no deploy of it could be verified")


def test_the_registry_path_is_lower_case():
    """A registry path must be lower-case and this owner is not.
    `github.repository` would publish `.../claudeMCP`, and every pull would
    404 on a name that reads as correct — the exact shape of failure this
    file exists for."""
    body = IMAGE.read_text()
    assert "${{ github.repository }}" not in body, (
        "the image is tagged from github.repository, which contains capital "
        "letters; the push or every later pull will fail on the name")
    for line in body.splitlines():
        if "ghcr.io/" in line:
            path = line.split("ghcr.io/")[1].split(":")[0].strip()
            assert path == path.lower(), f"not a valid registry path: {path}"


def test_the_monitor_pings_the_host_that_is_actually_live():
    """keepalive.yml pinged the retired Render domain every ten minutes for
    weeks, got a non-200 every time, and deliberately exited 0. A monitor
    that cannot tell "the service is down" from "I am asking the wrong
    service" reports nothing, at a cost of 4,300 requests a month."""
    body = (WORKFLOWS / "keepalive.yml").read_text()
    assert "onrender.com" not in body, (
        "the keep-alive still defaults to the retired Render host")


def test_the_guide_says_how_to_tell_whether_a_deploy_landed():
    """The operator had no way to answer "is my fix live?" and so could not
    stop a wrong diagnosis from compounding. It is the first question when
    a fix appears not to work, so it belongs in the deploy guide."""
    guide = (ROOT / "docs" / "DEPLOY.md").read_text()
    assert "build_detail" in guide
    assert "Auto deploy unavailable" in guide, (
        "the guide does not describe the failure that cost three days, so "
        "the next person reads the branch setting and finds it correct")
    assert "RAILWAY_TOKEN" in guide, (
        "the guide does not say how to set up the push-based deploy it needs")
