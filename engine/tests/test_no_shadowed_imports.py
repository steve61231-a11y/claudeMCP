"""A route function must never shadow a module the code depends on.

`from engine import health` and `def health():` in the same module means the
NAME health is rebound to the route function at import time. Every later
`health.something` then raises AttributeError.

That happened in the error handler of _run_report_job:

    except Exception as exc:
        if isinstance(exc, health.PreflightFailed):   # AttributeError

An exception inside an except block propagates out of the worker thread. The
job dict was never updated, so it stayed status="running" with no partial and
no error — forever. The page polled a job that no longer had anything running
behind it and showed the last stage text for as long as the reader waited.

It only fires when the run raises, which is why every local run succeeded and
every throttled one hung.
"""

import ast
import pathlib

import pytest

ENGINE = pathlib.Path(__file__).resolve().parent.parent
MODULES = sorted(p for p in ENGINE.rglob("*.py")
                 if "tests" not in p.parts and "alembic" not in p.parts)


def _shadowed(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text())
    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported[alias.asname or alias.name.split(".")[0]] = alias.name
    clashes = []
    for node in tree.body:  # module level only — a local name shadows nothing
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in imported:
                clashes.append(f"{node.name} (imported as {imported[node.name]})")
    return clashes


@pytest.mark.parametrize("path", MODULES, ids=lambda p: str(p.relative_to(ENGINE)))
def test_no_module_level_definition_shadows_an_import(path):
    clashes = _shadowed(path)
    assert not clashes, (
        f"{path.relative_to(ENGINE)} defines {clashes}, rebinding a name the module "
        "imports. Every later use of that name resolves to the definition, not the "
        "import — and if it is inside an except block, the worker thread dies "
        "silently and the job hangs forever.")


def test_the_preflight_handler_can_actually_reach_the_exception_class():
    """The specific failure: the handler that turns a dead model into an
    operator instruction could not name the exception it catches."""
    from engine import api_server

    assert api_server.health_mod.PreflightFailed
    assert api_server.stages_mod.current()


def test_a_failing_run_marks_the_job_failed_instead_of_hanging(monkeypatch, db_session):
    """The observable consequence: a run that raises must leave the job in a
    terminal state. It used to stay "running" with no error, forever."""
    import time

    from engine import api_server

    monkeypatch.setattr(api_server, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(api_server, "_save_progress", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_set_stage", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_lookup_precache", lambda name: None)
    monkeypatch.setattr(api_server, "_ensure_politician",
                        lambda db, name, t: (_ for _ in ()).throw(RuntimeError("boom")))

    job_id = "failing"
    api_server._jobs[job_id] = {"status": "running", "created_at": time.time()}
    try:
        api_server._run_report_job(job_id, "Someone", "politician", 30)
        job = api_server._jobs[job_id]
        assert job["status"] == "done", "a raised run must not leave the job running"
        assert job.get("ok") is False
        assert job.get("error")
    finally:
        api_server._jobs.pop(job_id, None)
