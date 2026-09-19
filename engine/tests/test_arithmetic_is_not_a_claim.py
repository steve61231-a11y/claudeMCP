"""Ten pages of a live report doubting its own arithmetic.

The verification stage audited the report's prose against the stored corpus,
which is right — except that the analysts are HANDED the sentiment split, the
per-platform volumes and the narrative scores (reports/sections.py
`_context_blob`) and asked to write trends "with the numbers that show it".
They did. Verification then took those numbers and went looking for a news
article that confirms them:

    Facebook has 55 mentions.            unverified · 21% conf.
    Overall sentiment is 76.1% neutral.  unverified · 21% conf.
    YouTube has 20 mentions.             unverified · 28% conf.

There is no such article. We counted those rows ourselves. Roughly a fifth of
that report was the system failing to corroborate its own arithmetic — and the
damage is not the wasted calls, it is that an executive reads "Overall
sentiment is 76.1% neutral — UNVERIFIED" and concludes the sentiment analysis
cannot be trusted. It can; it was audited against the wrong oracle.

The three claims in that same run that WERE about the world came back verified
at 91%, 61% and 57%. The checker works. It was being fed the wrong input.
"""

import pytest

from engine.agents import verify


@pytest.mark.parametrize("claim", [
    "Facebook has 55 mentions.",
    "Overall sentiment is 76.1% neutral.",
    "YouTube's 20 mentions represent 18.3% of total volume.",
    "The narrative 'Mudavadi multilateral and aviation push' has a growth rate of 45.",
    "'Citizen TV Kenya' has an influence driver score of 7.7.",
    "Facebook's sentiment contribution is +16.0.",
    "Facebook dominates volume at 55 mentions.",
    "There are 109 total mentions.",
])
def test_our_own_arithmetic_is_recognised(claim):
    """Verbatim from the live report that prompted this."""
    assert verify._is_our_own_measurement(claim), claim


@pytest.mark.parametrize("claim", [
    "Musalia Mudavadi serves as Prime Cabinet Secretary.",
    "Mudavadi paid $29 million to buy back insurers he sold to Barclays.",
    "Mudavadi launched the Magharibi Movement ahead of the 2027 polls.",
    "He was born on 21 September 1960.",
    "Sentiment turned against him after the Korea trip.",
    "Kenya and Morocco inked 11 instruments of cooperation.",
])
def test_a_claim_about_the_world_still_gets_checked(claim):
    """Conservative on purpose. Wrongly skipping a world-claim hides a real
    hallucination, which is far worse than one stray "unverified" — note that
    "Kenya and Morocco inked 11 instruments" carries a number and must still
    be checked."""
    assert not verify._is_our_own_measurement(claim), claim


def test_a_metric_word_without_a_number_is_not_a_measurement():
    """"Sentiment" alone is ordinary English. The pair — our vocabulary AND a
    figure — is what marks a sentence as describing the dataset."""
    assert not verify._is_our_own_measurement("Public sentiment is hardening.")
    assert not verify._is_our_own_measurement("His mentions of reform are constant.")


def test_measurements_are_counted_not_silently_dropped():
    """The codebase's standing rule: something removed from a report must be
    disclosed, or a thinner file looks like a quieter subject."""
    import inspect

    source = inspect.getsource(verify.verify_payload)
    assert "measurements" in source
    assert '"measurements": measurements' in source


def test_the_extractor_is_told_not_to_produce_them():
    """Filtering after the fact still pays for the extraction. The prompt is
    the cheaper place to stop it."""
    # BOTH prompts. The batched one is what actually runs in production, and
    # editing only the single-passage one would have changed nothing —
    # precisely the "fix the instance, not the rule" trap this codebase has
    # paid for repeatedly.
    import re as _re

    for prompt in (verify.EXTRACT_PROMPT, verify.BATCH_EXTRACT_PROMPT):
        flat = _re.sub(r"\s+", " ", prompt).lower()   # the example wraps a line
        assert "measurement" in flat
        assert "facebook has 55 mentions" in flat
