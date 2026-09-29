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


def test_repeated_url_gets_its_own_snippet_not_the_first_occurrences():
    # A URL cited twice must get two DIFFERENT snippets, each reflecting its own
    # surrounding text -- text.find(url) would return the first occurrence's
    # position for both, silently duplicating the wrong context onto the second.
    # Filler is wide enough (1000 chars) that the two +-200-char snippet windows
    # never overlap.
    url = "https://example.com/paper"
    filler = "X" * 1000
    text = f"MARKER_ONE {url} {filler} MARKER_TWO {url} MARKER_THREE"
    result = RunnerResult("claude", True, 0, stdout=text, stderr="")
    sources = _sources_from_adversary_output(result, question="q")
    assert len(sources) == 2
    assert "MARKER_ONE" in sources[0].content
    assert "MARKER_TWO" not in sources[0].content
    assert "MARKER_TWO" in sources[1].content
    assert "MARKER_THREE" in sources[1].content
    assert "MARKER_ONE" not in sources[1].content
