"""Leave-one-out ablation tests — 'does this source actually add value?'

Verified locally: these pin the marginal-value verdicts that a standalone-score
evict rule gets WRONG — a low-authority sole-cover source is load-bearing, a
high-authority source with a redundant twin is not uniquely valuable, and pure
noise is cuttable. Offline/deterministic — no network, keys, or CLIs.
"""
from notelab.research.quality import SourceFeatures
from notelab.research.ablation import ablate_sources


def _s(i, dom, text, n=120):
    return SourceFeatures(id=i, url=f"https://{dom}/{i}", content=(text + " ") * n)


Q = "quantum error correction surface code threshold decoder"


def _fixture():
    return [
        _s("A", "arxiv.org",  "quantum error correction surface code threshold"),
        _s("B", "nature.com", "quantum error correction surface code threshold"),
        _s("C", "some-lab-blog.org", "decoder algorithm belief propagation"),
        _s("D", "top10-quantum-deals.com", "buy cheap quantum stuff now discount", n=2),
    ]


def test_sole_cover_source_is_load_bearing_even_if_low_authority():
    rep = ablate_sources(_fixture(), Q)
    c = {v.id: v for v in rep.per_source}["C"]
    assert c.verdict == "load-bearing"
    assert "decoder" in c.sole_cover_terms
    assert c.coverage_delta > 0          # removing C loses question coverage
    assert "C" in rep.keep_locked
    assert "C" not in rep.cut_candidates


def test_pure_noise_source_is_cuttable():
    rep = ablate_sources(_fixture(), Q)
    d = {v.id: v for v in rep.per_source}["D"]
    assert d.verdict != "load-bearing"
    assert d.coverage_delta == 0.0
    assert "D" in rep.cut_candidates


def test_redundant_twin_loses_no_coverage_when_removed():
    # A and B cover identical terms; removing EITHER keeps full coverage.
    rep = ablate_sources(_fixture(), Q)
    by = {v.id: v for v in rep.per_source}
    assert by["A"].coverage_delta == 0.0
    assert by["B"].coverage_delta == 0.0


def test_full_coverage_and_report_shape():
    rep = ablate_sources(_fixture(), Q)
    assert rep.full_coverage == 1.0      # the set covers every question term
    assert rep.n_sources == 4
    d = rep.as_dict()
    assert d["per_source"][0]["verdict"] in {"load-bearing", "marginal", "redundant"}


def test_empty_set_is_safe():
    rep = ablate_sources([], Q)
    assert rep.n_sources == 0
    assert rep.per_source == []
    assert rep.cut_candidates == [] and rep.keep_locked == []
