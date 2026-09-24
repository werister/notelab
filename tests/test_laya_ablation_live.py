"""Live correctness test for the Laya ablation tie-breaker (research/laya_judge.py).

Gated on laya_available() (skip, don't fail, when laya-mlx isn't installed) --
mirrors tests/test_semantic_expansions.py's MISTRAL_API_KEY gate. Uses a fixture
with a genuine, defensible ground truth -- one source carrying specific facts vs.
two sources that only restate the topic's keywords as vague filler -- to assert
Laya gets it RIGHT where the fixed-epsilon threshold gets it wrong (FIELD-NOTES.md
gap #8), not just "returns a verdict, any verdict" (the bar Copilot's review
flagged the original draft as not meeting).
"""
import pytest

from notelab.research.ablation import ablate_sources
from notelab.research.laya_judge import laya_available
from notelab.research.quality import SourceFeatures


def _s(i, dom, text, n=1):
    return SourceFeatures(id=i, url=f"https://{dom}/{i}", content=(text + " ") * n)


Q = "PSD2 aggregator BaFin regulation for German fintech"


def _strong_weak_fixture():
    # "strong" carries specific, checkable facts (names, numbers, a concrete
    # policy detail). Both "weak" sources are twins that only restate the
    # question's own keywords as vague filler -- genuinely less valuable, not
    # just shorter. Verified offline: all three land in the ambiguous zone
    # (deltas -0.0141 to 0.0071, all under composite_eps=0.02), i.e. the
    # threshold alone can't tell "strong" apart from the filler twins.
    strong = ("finAPI and Tink both hold a PSD2 license from BaFin; GoCardless "
              "Bank Account Data covers 2500 German banks with a 90-day consent "
              "window and charges per-account pricing tiers for aggregator access.")
    weak = "this covers PSD2 aggregator BaFin regulation for German fintech topics."
    return [
        _s("strong", "finapi.io", strong, n=15),
        _s("weak_a", "site1.com", weak, n=15),
        _s("weak_b", "site2.com", weak, n=15),
    ]


@pytest.mark.skipif(not laya_available(),
                    reason="live Laya test needs laya-mlx installed (pip install 'notelab[laya]')")
def test_laya_correctly_judges_strong_source_load_bearing_where_threshold_fails():
    # confirm the deterministic threshold alone still gets this wrong (the bug
    # gap #8 documents) before checking Laya fixes it
    rep_off = ablate_sources(_strong_weak_fixture(), Q, use_laya=False)
    strong_off = {v.id: v for v in rep_off.per_source}["strong"]
    assert strong_off.verdict == "redundant"  # the documented bug, still present

    rep_on = ablate_sources(_strong_weak_fixture(), Q, use_laya=True)
    strong_on = {v.id: v for v in rep_on.per_source}["strong"]
    assert strong_on.verdict == "load-bearing"
    assert strong_on.judge_confidence is not None
