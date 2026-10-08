"""Report / export generation — offline, deterministic."""
from notelab.research.ablation import quick_assess
from notelab.research.quality import SourceFeatures, score_sources, preset
from notelab.research.report import build_report_model, render, render_csv, render_markdown


def _scored(srcs, q, w="project_kickstart"):
    return score_sources(srcs, q, preset(w)).as_dict()


def test_markdown_report_surfaces_blocked_videos_and_actions():
    srcs = [
        SourceFeatures("p", "paper", "https://arxiv.org/abs/1", "attention " * 200),
        SourceFeatures("b", "vid - YouTube", "https://youtube.com/watch?v=x",
                       content="Sign in Skip navigation Search with your voice "
                               "Sign in to confirm you're not a bot " * 150),
    ]
    q = "how transformers attention works"
    model = build_report_model(_scored(srcs, q), notebook_id="nb", question=q,
                               assess=quick_assess(srcs, q, mode="fast"))
    md = render(model, "markdown")
    # the blocked video must be flagged AND produce a fix_videos action
    assert "blocked" in md.lower()
    assert "research_fix_videos" in md
    assert "## Next actions" in md
    assert "nb" in md


def test_report_recommends_fixing_a_lopsided_mix():
    # all web, no paper/video/discussion -> should suggest adding missing kinds
    srcs = [SourceFeatures(f"w{i}", "blog", f"https://blog{i}.example/x", "stuff " * 100)
            for i in range(5)]
    q = "how to start a project"
    model = build_report_model(_scored(srcs, q), question=q,
                               assess=quick_assess(srcs, q, mode="fast"))
    actions = " ".join(model["next_actions"]).lower()
    assert "missing" in actions or "video" in actions or "paper" in actions


def test_clean_notebook_reports_no_blocking_issues():
    srcs = [
        SourceFeatures("p", "paper", "https://arxiv.org/abs/1", "quantum error correction " * 200),
        SourceFeatures("v", "vid", "https://youtube.com/watch?v=y",
                       content="in this video quantum error correction surface codes " * 200),
        SourceFeatures("d", "reddit", "https://reddit.com/r/x/y",
                       content="community quantum error correction notes " * 150),
    ]
    q = "quantum error correction"
    model = build_report_model(_scored(srcs, q), question=q,
                               assess=quick_assess(srcs, q, mode="fast"))
    findings = " ".join(model["findings"]).lower()
    assert "no blocking issues" in findings


def test_csv_has_one_row_per_source_plus_header():
    srcs = [SourceFeatures("a", "x", "https://arxiv.org/abs/1", "y " * 50),
            SourceFeatures("b", "x", "https://github.com/o/r", "z " * 50)]
    model = build_report_model(_scored(srcs, "topic"))
    rows = render_csv(model).strip().splitlines()
    assert rows[0].startswith("id,kind,domain")
    assert len(rows) == 3  # header + 2 sources


def test_json_format_is_the_model():
    import json
    srcs = [SourceFeatures("a", "x", "https://arxiv.org/abs/1", "y " * 50)]
    model = build_report_model(_scored(srcs, "topic"), notebook_id="nb")
    out = json.loads(render(model, "json"))
    assert out["notebook_id"] == "nb"
    assert "next_actions" in out and "findings" in out


def test_unknown_format_raises():
    import pytest
    model = build_report_model(_scored([SourceFeatures("a", "x", "https://a.com/1", "y")], "t"))
    with pytest.raises(ValueError):
        render(model, "pdf")
