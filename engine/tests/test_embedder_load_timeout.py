"""The "Narratives" step that never finished.

`SentenceTransformer("all-MiniLM-L6-v2")` downloads the model on first use.
On a cold instance with a slow or blocked route to the HuggingFace hub that
call can hang for a very long time or never return — and it sits inside the
narrative-building stage, after sentiment has already been scored, so the
report gets stuck ticking "Narratives" with nothing to show and no error.

The fallback for a FAILED load already existed (TF-IDF). What was missing was
a bound on a load that never finishes.

A note on how these are asserted. They used to measure wall-clock and require
the call to return in under five seconds, which made them fail roughly one run
in three when the whole suite was running — not because the bound was missed,
but because the FALLBACK is itself slow to start: importing sklearn and
fitting a TfidfVectorizer costs seconds on a loaded machine. The five was
arbitrary and had nothing to do with the defect.

What the defect actually is: the run WAITED for a load that was never going to
finish. So assert that — the call returns in a fraction of the hang, and the
stage ledger says the fallback was taken. Both are about the behaviour rather
than about how fast this machine happens to be today.
"""

import sys
import time
import types

import pytest

from engine import stages
from engine.intelligence import narratives as N

#: How long the stand-in model pretends to hang. The assertions below compare
#: against THIS, not against a fixed number of seconds: returning in a
#: fraction of it is what proves the wait was abandoned, and the margin is
#: wide enough that a busy machine cannot turn a pass into a failure.
HANG_SECONDS = 30.0


@pytest.fixture(autouse=True)
def _clean_state():
    N.reset_embedder_state()
    stages.reset()
    yield
    N.reset_embedder_state()


def _fell_back_to_tfidf() -> bool:
    """Did the fallback actually run? The stage ledger records it, which is a
    fact about what happened rather than about how long it took."""
    return any("TF-IDF" in (row.get("detail") or "")
               for row in stages.current().summary().get("stages", []))


def _install_hanging_transformer(delay=30.0):
    mod = types.ModuleType("sentence_transformers")

    class _Hangs:
        def __init__(self, *a, **k):
            time.sleep(delay)

    mod.SentenceTransformer = _Hangs
    sys.modules["sentence_transformers"] = mod


def _install_working_transformer():
    mod = types.ModuleType("sentence_transformers")

    class _Fast:
        def __init__(self, *a, **k):
            pass

        def encode(self, texts, show_progress_bar=False):
            import numpy as np

            return np.zeros((len(texts), 4))

    mod.SentenceTransformer = _Fast
    sys.modules["sentence_transformers"] = mod


def test_a_load_that_never_finishes_falls_back_within_the_timeout(monkeypatch):
    from engine.config import settings

    monkeypatch.setattr(settings, "use_local_ml", True)
    monkeypatch.setattr(N, "EMBEDDER_LOAD_TIMEOUT", 0.5)
    _install_hanging_transformer(delay=HANG_SECONDS)

    started = time.monotonic()
    out = N.embed_texts(["a bb ccc", "b cc dd", "c dd ee"])
    elapsed = time.monotonic() - started

    assert _fell_back_to_tfidf(), "the hung load was not abandoned for the fallback"
    assert elapsed < HANG_SECONDS / 2, (
        f"returned in {elapsed:.1f}s — that is the hung download being waited on, "
        f"not the {N.EMBEDDER_LOAD_TIMEOUT}s timeout being honoured")
    assert out.shape[0] == 3


def test_narrative_clustering_survives_a_hung_embedder(monkeypatch):
    from engine.config import settings

    monkeypatch.setattr(settings, "use_local_ml", True)
    monkeypatch.setattr(N, "EMBEDDER_LOAD_TIMEOUT", 0.3)
    _install_hanging_transformer(delay=HANG_SECONDS)

    started = time.monotonic()
    labels = N.cluster_mentions(["a bb ccc", "b cc dd", "c dd ee", "totally different topic here"])
    elapsed = time.monotonic() - started

    assert _fell_back_to_tfidf()
    assert elapsed < HANG_SECONDS / 2
    assert len(labels) == 4


def test_a_late_finishing_load_is_adopted_not_wasted(monkeypatch):
    """The load keeps running on its thread after we give up waiting. The next
    call should pick up the finished model instead of paying for TF-IDF again."""
    from engine.config import settings

    monkeypatch.setattr(settings, "use_local_ml", True)
    monkeypatch.setattr(N, "EMBEDDER_LOAD_TIMEOUT", 0.2)
    _install_hanging_transformer(delay=0.6)

    with pytest.raises(TimeoutError):
        N.get_embedder()

    # Poll rather than sleeping a fixed second: the background thread finishes
    # when it finishes, and a machine under load can take longer than any
    # constant someone picks here.
    deadline = time.monotonic() + 15.0
    model = None
    while time.monotonic() < deadline:
        try:
            model = N.get_embedder()
            break
        except TimeoutError:
            time.sleep(0.05)
    assert model is not None, "the finished load was never adopted"


def test_a_fast_load_is_used_directly(monkeypatch):
    from engine.config import settings

    monkeypatch.setattr(settings, "use_local_ml", True)
    monkeypatch.setattr(N, "EMBEDDER_LOAD_TIMEOUT", 5.0)
    _install_working_transformer()

    out = N.embed_texts(["a bb ccc", "b cc dd", "c dd ee"])

    # The shape IS the assertion: (3, 4) is the stand-in embedder's own output.
    # TF-IDF would return 512 columns, so this cannot pass by falling back —
    # and it says so without timing anything.
    assert out.shape == (3, 4)
    assert not _fell_back_to_tfidf(), "a working model was abandoned for TF-IDF"
