"""The "Narratives" step that never finished.

`SentenceTransformer("all-MiniLM-L6-v2")` downloads the model on first use.
On a cold instance with a slow or blocked route to the HuggingFace hub that
call can hang for a very long time or never return — and it sits inside the
narrative-building stage, after sentiment has already been scored, so the
report gets stuck ticking "Narratives" with nothing to show and no error.

The fallback for a FAILED load already existed (TF-IDF). What was missing was
a bound on a load that never finishes.
"""

import sys
import time
import types

import pytest

from engine.intelligence import narratives as N


@pytest.fixture(autouse=True)
def _clean_state():
    N.reset_embedder_state()
    yield
    N.reset_embedder_state()


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
    _install_hanging_transformer(delay=30.0)

    started = time.monotonic()
    out = N.embed_texts(["a bb ccc", "b cc dd", "c dd ee"])
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, "the run waited for the hung download instead of falling back"
    assert out.shape[0] == 3


def test_narrative_clustering_survives_a_hung_embedder(monkeypatch):
    from engine.config import settings

    monkeypatch.setattr(settings, "use_local_ml", True)
    monkeypatch.setattr(N, "EMBEDDER_LOAD_TIMEOUT", 0.3)
    _install_hanging_transformer(delay=30.0)

    started = time.monotonic()
    labels = N.cluster_mentions(["a bb ccc", "b cc dd", "c dd ee", "totally different topic here"])
    elapsed = time.monotonic() - started

    assert elapsed < 5.0
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

    time.sleep(1.0)  # let the background thread finish
    model = N.get_embedder()
    assert model is not None


def test_a_fast_load_is_used_directly(monkeypatch):
    from engine.config import settings

    monkeypatch.setattr(settings, "use_local_ml", True)
    monkeypatch.setattr(N, "EMBEDDER_LOAD_TIMEOUT", 5.0)
    _install_working_transformer()

    started = time.monotonic()
    out = N.embed_texts(["a bb ccc", "b cc dd", "c dd ee"])
    elapsed = time.monotonic() - started

    assert elapsed < 5.0
    assert out.shape == (3, 4)
