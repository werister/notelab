"""_sources_from_adversary_output() -- turning one adversarial CLI's raw stdout into
SourceFeatures score_sources() can rank. Offline, deterministic."""
from notelab.research.evals import RunnerResult
from notelab.research.sparring import _sources_from_adversary_output


def test_extracts_sources_from_cited_urls():
    result = RunnerResult("opencode", True, 0,
                          stdout="Found this: https://arxiv.org/abs/1234 which covers it well.",
                          stderr="")
    sources = _sources_from_adversary_output(result, question="q")
    assert len(sources) == 1
    assert sources[0].url == "https://arxiv.org/abs/1234"
    assert "arxiv.org" in sources[0].content


def test_extracts_multiple_cited_urls():
    result = RunnerResult("claude", True, 0,
                          stdout="See https://a.example/1 and also https://b.example/2 for more.",
                          stderr="")
    sources = _sources_from_adversary_output(result, question="q")
    assert {s.url for s in sources} == {"https://a.example/1", "https://b.example/2"}


def test_wraps_whole_answer_when_no_urls_cited():
    result = RunnerResult("cline", True, 0, stdout="The answer is 42, no links needed.", stderr="")
    sources = _sources_from_adversary_output(result, question="q")
    assert len(sources) == 1
    assert sources[0].url == ""
    assert "42" in sources[0].content


def test_empty_output_yields_no_sources():
    result = RunnerResult("kilo", True, 0, stdout="", stderr="")
    sources = _sources_from_adversary_output(result, question="q")
    assert sources == []
