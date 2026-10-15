"""Fifty minutes, stuck at 12 of 15 sections.

A live run on build 464cd2d — which already had the clusterer cap, and got
much further because of it — reached twelve sections and then sat there.
The run ceiling is 1800 seconds. It was still going at fifty minutes.

`out_of_time()` stops a stage being ENTERED. It does nothing once inside.
So a stage entered at minute twenty-nine of a thirty-minute ceiling runs
for as long as it likes, and the three stages after the analyst fan-out —
entity/event resolution, the sentiment framework, claim verification — were
each gated on entry and then unbounded.

That is this repository's own rule, written down and then not applied to
the last three stages in the file: "try/except does not bound a call that
never returns — there is no exception to catch." `_bounded` already exists
for exactly this, is already how the analyst fan-out is held to its clock,
and its docstring already names the symptom: the work is not failing, it is
simply not coming back.

A gate is not a ceiling.
"""

import ast
from pathlib import Path

PIPELINE_SRC = (Path(__file__).resolve().parents[1] / "pipeline.py").read_text()
TREE = ast.parse(PIPELINE_SRC)
RUN = next(n for n in ast.walk(TREE)
           if isinstance(n, ast.FunctionDef) and n.name == "run_analysis")

TAIL = ("entity_event_resolution", "sentiment_framework", "claim_verification")


def test_the_tail_stages_are_bounded_not_only_gated():
    for name in TAIL:
        assert f'_bounded("{name}"' in PIPELINE_SRC, (
            f"{name} is entered behind out_of_time() and then runs without a "
            "ceiling, so a stage entered just before the deadline can run "
            "for as long as it likes")


def test_each_is_handed_what_is_left_of_the_run():
    """Not a fresh budget each. Three stages each given the full ceiling is
    the same unbounded run wearing three hats — the mistake
    `LLM_MAX_CONCURRENCY` was introduced to fix, one layer up."""
    assert "_time_left()" in PIPELINE_SRC
    assert PIPELINE_SRC.count("_bounded(") >= len(TAIL)
    body = PIPELINE_SRC[PIPELINE_SRC.index("def _time_left"):][:600]
    assert "run_ends_at - _time.monotonic()" in body


def test_a_stage_is_never_handed_nothing():
    """Zero seconds is a different way of skipping, and the ledger would
    then blame a timeout for a decision the clock made."""
    body = PIPELINE_SRC[PIPELINE_SRC.index("def _time_left"):][:600]
    assert "max(minimum" in body
    assert "minimum: float = 30.0" in body


def test_no_variable_is_trapped_in_the_new_closures():
    """The one real hazard of wrapping a block in a function: anything
    assigned inside and read after the block becomes local, and the read
    after it raises NameError at runtime — long after the tests that
    exercise the stage itself have passed."""
    inner = [n for n in ast.walk(RUN)
             if isinstance(n, ast.FunctionDef) and n.name.startswith("_run_")]
    assert inner, "the tail stages are no longer wrapped at all"
    for node in inner:
        assigned = {t.id for a in ast.walk(node) if isinstance(a, ast.Assign)
                    for t in ast.walk(a)
                    if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store)}
        read_after = {n2.id for n2 in ast.walk(RUN)
                      if isinstance(n2, ast.Name) and isinstance(n2.ctx, ast.Load)
                      and n2.lineno > node.end_lineno}
        trapped = sorted(assigned & read_after)
        assert not trapped, (
            f"{node.name} assigns {trapped} and something after the block "
            "reads them — they are local to the closure now")


def test_the_bounded_runner_is_the_shared_one():
    """Not a second implementation. The analyst fan-out's version already
    abandons the worker without waiting, which is the whole point."""
    assert "from engine.reports.sections import _bounded" in PIPELINE_SRC
    sections = (Path(__file__).resolve().parents[1]
                / "reports" / "sections.py").read_text()
    runner = sections[sections.index("def _bounded"):
                  sections.index("def _collect_quotes")]
    assert "pool.shutdown(wait=False)" in runner, (
        "the runner waits on the abandoned worker, so the ceiling does not "
        "actually return control")
