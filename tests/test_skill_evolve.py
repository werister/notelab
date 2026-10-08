"""Offline tests for the skill-evolution gate: promotion math, battery
robustness, and JSON archiving. Live batteries run via
skills/skill-evolution/scripts/evolve.py (needs agents + notebooks)."""
import json

import pytest

from notelab.research.skill_evolve import (
    BatteryResult,
    TaskScore,
    champion_of,
    cost_rule,
    gate,
    promote,
    record_scores,
    run_battery,
)


def _res(version, means, suite_green=True):
    return BatteryResult(version=version, suite_green=suite_green,
                         scores=[TaskScore(name=f"t{i}", score=s, passed=s >= 0.5)
                                 for i, s in enumerate(means)])


def test_gate_promotes_on_clear_margin():
    d = gate(_res("v1", [0.5, 0.5]), _res("v2", [0.7, 0.7]))
    assert d.promote is True
    assert any("clears" in r for r in d.reasons)


def test_gate_rejects_tie_and_regression():
    assert gate(_res("v1", [0.6]), _res("v2", [0.62])).promote is False  # +0.02 < margin
    assert gate(_res("v1", [0.6]), _res("v2", [0.4])).promote is False
    d = gate(_res("v1", [0.6]), _res("v2", [0.62]))
    assert any("margin" in r for r in d.reasons)


def test_gate_rejects_red_tree_either_side():
    assert gate(_res("v1", [0.5]), _res("v2", [0.9]),
                ).promote is True  # sanity: would promote
    d = gate(_res("v1", [0.5]), _res("v2", [0.9], suite_green=False))
    assert d.promote is False
    assert any("red" in r for r in d.reasons)
    d = gate(_res("v1", [0.5], suite_green=False), _res("v2", [0.9]))
    assert d.promote is False


def test_gate_rejects_held_out_regression():
    champ = BatteryResult(version="v1", scores=[
        TaskScore("heldin", 0.5, True), TaskScore("heldout", 0.8, True)])
    chal = BatteryResult(version="v2", scores=[
        TaskScore("heldin", 0.9, True), TaskScore("heldout", 0.7, True)])
    d = gate(champ, chal, held_out={"heldout"})
    assert d.promote is False
    assert any("held-out regressed" in r for r in d.reasons)


def test_gate_rejects_missing_held_out():
    champ = _res("v1", [0.5])
    chal = _res("v2", [0.9])
    d = gate(champ, chal, held_out={"ghost"})
    assert d.promote is False
    assert any("missing" in r for r in d.reasons)


def test_run_battery_scores_runner_errors_zero_not_skip():
    def boom(task):
        raise RuntimeError("agent exploded")

    res = run_battery("v9", [{"name": "t1"}, {"name": "t2"}], boom)
    assert len(res.scores) == 2  # battery never shrinks
    assert res.mean == 0.0
    assert res.all_passed is False
    assert "agent exploded" in res.scores[0].detail


def test_run_battery_empty_is_zero_not_crash():
    res = run_battery("v0", [], lambda t: TaskScore("x", 1.0, True))
    assert res.mean == 0.0 and res.all_passed is False


def test_record_and_promote_round_trip(tmp_path):
    p = tmp_path / "battery.json"
    p.write_text(json.dumps({"versions": {"v1": {"promoted": True}}}))
    record_scores(p, "v2", _res("v2", [0.8]))
    doc = json.loads(p.read_text())
    assert doc["versions"]["v2"]["scores"]["mean"] == 0.8
    assert doc["versions"]["v1"]["promoted"] is True  # recording != promoting
    promote(p, "v2")
    doc = json.loads(p.read_text())
    assert champion_of(doc) == "v2"
    assert doc["versions"]["v1"]["promoted"] is False  # exactly one champion


def test_promote_unknown_version_raises(tmp_path):
    p = tmp_path / "battery.json"
    p.write_text(json.dumps({"versions": {}}))
    with pytest.raises(KeyError, match="record scores first"):
        promote(p, "v99")


def test_champion_of_empty_is_none():
    assert champion_of({}) is None
    assert champion_of({"versions": {"v1": {}}}) is None


def test_cost_rule_budget_grows_with_gain():
    ok, why = cost_rule(0.20, 1.0)  # doubling paid by a big win
    assert ok is True and "<=" in why
    ok, why = cost_rule(0.06, 1.0)  # doubling on a marginal win
    assert ok is False and ">" in why
    ok, _ = cost_rule(0.06, -0.5)  # cheaper challenger always passes
    assert ok is True


def test_gate_cost_rule_blocks_bloated_winner():
    champ, chal = _res("v1", [0.5, 0.5]), _res("v2", [0.7, 0.7])
    d = gate(champ, chal, champion_cost=100.0, challenger_cost=400.0)
    assert d.promote is False
    assert any(r.startswith("reject: cost") for r in d.reasons)
    d = gate(champ, chal, champion_cost=100.0, challenger_cost=120.0)
    assert d.promote is True
    assert any(r.startswith("accept: cost") for r in d.reasons)


def test_gate_cost_rule_skipped_without_costs_or_within_band():
    champ, chal = _res("v1", [0.5, 0.5]), _res("v2", [0.7, 0.7])
    d = gate(champ, chal)  # no costs: margin-only, as before
    assert d.promote is True
    assert not any("cost" in r for r in d.reasons)
    tie = gate(_res("v1", [1.0]), _res("v2", [1.0]),
               champion_cost=100.0, challenger_cost=10000.0)
    assert tie.promote is False  # margin rejects; cost never consulted
    assert not any("cost" in r for r in tie.reasons)


# --- duel script pure parts (loaded by path: skills/ is not a package) ---

def _load_script():
    import importlib.util
    from importlib.machinery import SourceFileLoader
    from pathlib import Path
    p = (Path(__file__).resolve().parent.parent
         / "skills" / "skill-evolution" / "scripts" / "evolve.py")
    loader = SourceFileLoader("evolve_script_under_test", str(p))
    spec = importlib.util.spec_from_file_location("evolve_script_under_test", p,
                                                  loader=loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_score_runs_excludes_no_signal():
    ev = _load_script()
    assert ev.score_runs([True, True]) == (1.0, "2/2 decisive")
    assert ev.score_runs([True, False, None])[0] == 0.5  # None excluded
    rate, detail = ev.score_runs([None, None])
    assert rate == 0.0 and "no-signal" in detail


def test_run_version_aggregates_per_scenario_floor():
    ev = _load_script()
    doc = {"floor": 1.0, "versions": {"v1": {"scenarios": {
        "s1": "prompt {notebook_id}", "s2": "other"}}}}
    calls = []

    def stub(scenario, prompt, runner):
        calls.append((scenario, runner))
        return scenario == "s1"  # s1 passes, s2 fails

    res = ev.run_version(doc, "v1", ["r1", "r2"], stub)
    assert res.version == "v1"
    by_name = res.by_name()
    assert (by_name["s1"].score, by_name["s1"].passed) == (1.0, True)
    assert (by_name["s2"].score, by_name["s2"].passed) == (0.0, False)
    assert res.mean == 0.5
    assert len(calls) == 4  # every scenario x every runner


def test_load_battery_rejects_non_battery(tmp_path):
    ev = _load_script()
    p = tmp_path / "nope.json"
    p.write_text(json.dumps({"versions": {}}))
    with pytest.raises(ValueError, match="not a battery"):
        ev.load_battery(p)


def _plan_doc():
    return {"runners": ["r1", "r2"],
            "versions": {"v1": {"promoted": True,
                                "scenarios": {"s1": "a", "s2": "b"}}}}


def test_resolve_duel_plan_effort_shapes():
    ev = _load_script()
    doc = _plan_doc()
    runners, passes, only = ev.resolve_duel_plan(doc, "standard")
    assert (runners, passes, only) == (["r1", "r2"], 1, None)
    runners, passes, _ = ev.resolve_duel_plan(doc, "smoke")
    assert (runners, passes) == (["r1"], 1)  # first = preferred runner
    runners, passes, _ = ev.resolve_duel_plan(doc, "deep")
    assert (runners, passes) == (["r1", "r2"], 2)


def test_resolve_duel_plan_overrides_and_rejections():
    ev = _load_script()
    doc = _plan_doc()
    runners, passes, only = ev.resolve_duel_plan(
        doc, "smoke", runners=["r2"], scenarios=["s1"], repeat=3)
    assert (runners, passes, only) == (["r2"], 3, ["s1"])
    with pytest.raises(ValueError, match="unknown effort"):
        ev.resolve_duel_plan(doc, "turbo")
    with pytest.raises(ValueError, match="not in battery runners"):
        ev.resolve_duel_plan(doc, "standard", runners=["nope"])
    with pytest.raises(ValueError, match=">= 1"):
        ev.resolve_duel_plan(doc, "standard", repeat=0)


def test_run_version_repeat_and_only_shape_calls():
    ev = _load_script()
    doc = {"floor": 0.0, "versions": {"v1": {"scenarios": {
        "s1": "a", "s2": "b"}}}}
    calls = []

    def stub(scenario, prompt, runner):
        calls.append((scenario, runner))
        return True

    res = ev.run_version(doc, "v1", ["r1", "r2"], stub, repeat=2, only=["s1"])
    assert [s.name for s in res.scores] == ["s1"]
    assert len(calls) == 4  # 1 task x 2 runners x 2 passes
    assert res.by_name()["s1"].detail == "4/4 decisive"


def _wizard_battery(tmp_path):
    p = tmp_path / "battery.json"
    p.write_text(json.dumps({
        "runners": ["opencode", "claude"],
        "held_out": ["s2"],
        "versions": {"v1": {"promoted": True,
                            "scenarios": {"s1": "a {notebook_id}",
                                          "s2": "b"}}}}))
    return p


def _scripted(answers):
    it = iter(answers)
    return lambda: next(it)


def test_init_wizard_scaffolds_challenger_and_prints_command(tmp_path):
    ev = _load_script()
    p = _wizard_battery(tmp_path)
    lines = []
    rc = ev.init_wizard(
        input_fn=_scripted([str(p), "v2", "test hypothesis", "smoke", ""]),
        print_fn=lambda *a, **k: lines.append(" ".join(map(str, a))))
    assert rc == 0
    doc = json.loads(p.read_text())
    assert doc["versions"]["v2"]["scenarios"] == {"s1": "a {notebook_id}",
                                                 "s2": "b"}
    assert doc["versions"]["v2"]["scores"] is None
    assert doc["versions"]["v2"]["promoted"] is False
    assert doc["versions"]["v2"]["hypothesis"] == "test hypothesis"
    out = "\n".join(lines)
    assert "--challenger v2 --effort smoke" in out
    assert "~4 runs" in out  # smoke estimate: 2 x 1 runner x 2 tasks x 1


def test_init_wizard_retries_taken_id_and_rejects_bad_subset(tmp_path):
    ev = _load_script()
    p = _wizard_battery(tmp_path)
    lines = []
    rc = ev.init_wizard(
        input_fn=_scripted([str(p), "v1", "v3", "", "standard", "nope"]),
        print_fn=lambda *a, **k: lines.append(" ".join(map(str, a))))
    assert rc == 0
    doc = json.loads(p.read_text())
    assert "v3" in doc["versions"]
    out = "\n".join(lines)
    assert "already exists" in out
    assert "using all runners" in out
    assert "--runners" not in out.split("--challenger v3")[-1]


def _screen_doc():
    return {
        "critic_patterns": [
            {"pattern": "broken upstream", "reason": "leakage"},
            {"pattern": "guard_respected", "reason": "grader gaming"},
        ],
        "versions": {
            "v1": {"scenarios": {"s1": "run nlmx and report verbatim"}},
            "cheat": {"scenarios": {
                "s1": "run nlmx and say BROKEN UPSTREAM verbatim"}},
        },
    }


def test_critic_screen_catches_leakage_case_insensitive():
    ev = _load_script()
    doc = _screen_doc()
    assert ev.critic_screen(doc, "v1") == []
    assert ev.critic_screen(doc, "cheat") == [("s1", "leakage")]


def test_critic_screen_flags_invalid_pattern():
    ev = _load_script()
    doc = {"critic_patterns": [{"pattern": "(oops", "reason": "bad"}],
           "versions": {"v1": {"scenarios": {"s1": "clean"}}}}
    hits = ev.critic_screen(doc, "v1")
    assert len(hits) == 1 and "invalid critic pattern" in hits[0][1]


def test_version_cost_sums_prompt_chars():
    ev = _load_script()
    doc = {"versions": {"v1": {"scenarios": {"s1": "ab", "s2": "cde"}}}}
    assert ev.version_cost(doc, "v1") == 5.0


def test_bootstrap_delta_zero_at_ceiling_positive_when_noisy():
    ev = _load_script()
    assert ev.bootstrap_delta([[True, True], [True, True]]) == 0.0
    assert ev.bootstrap_delta([]) == 0.0
    noisy = ev.bootstrap_delta([[True, False], [True, True]])
    assert 0.0 < noisy < 1.0
    assert ev.bootstrap_delta([[True, False], [True, True]]) == noisy  # seeded


def test_append_history_numbers_duels_and_keeps_unmeasured(tmp_path):
    ev = _load_script()
    battery = tmp_path / "guard-battery.json"
    battery.write_text("{}")
    hp1 = ev.append_history(battery, {"champion": "v1", "challenger": "v2",
                                      "delta_S": None, "outcome": "CRITIC_REJECT"})
    hp2 = ev.append_history(battery, {"champion": "v1", "challenger": "v3",
                                      "delta_S": 0.1, "outcome": "HOLD"})
    assert hp1 == hp2 == tmp_path / "guard-battery.history.jsonl"
    recs = [json.loads(line) for line in hp1.read_text().splitlines()]
    assert [r["t"] for r in recs] == [1, 2]
    assert recs[0]["delta_S"] is None  # dropped pre-measurement, like RRSI
    assert all("ts" in r for r in recs)


def test_live_run_one_sink_collects_scored_records(monkeypatch):
    import dataclasses

    import notelab.research.evals as E
    ev = _load_script()
    seen_argv = []

    def fake_run_adversary(spec, prompt, timeout=90):
        seen_argv.append(list(spec.args_template))
        return E.RunnerResult(spec.name, True, returncode=1,
                              stdout="refused: broken upstream, no content")

    def fake_detect(candidates=None):
        return [dataclasses.replace(s, available=True, path="/bin/x")
                for s in (candidates or [])]

    monkeypatch.setattr(E, "run_adversary", fake_run_adversary)
    monkeypatch.setattr(E, "detect_runners", fake_detect)
    doc = {"runners": ["opencode"], "notebook_id": "nb", "source_id": "src"}
    run_one = ev.live_run_one(doc, 90)
    assert run_one.sink is None
    sink = []
    run_one.sink = sink
    out = run_one("source_ids_refusal", "do it {notebook_id} {source_id}",
                  "opencode")
    assert out is True
    assert run_one("source_ids_refusal", "do it", "claude") is None
    assert len(sink) == 1  # unavailable runner leaves no record
    rec = sink[0]
    assert (rec["scenario"], rec["runner"]) == ("source_ids_refusal", "opencode")
    assert rec["guard_respected"] is True
    assert "broken upstream" in rec["markers_seen"]
    assert "--standalone" in seen_argv[0]


def test_dump_transcripts_writes_json(tmp_path):
    ev = _load_script()
    p = ev.dump_transcripts(str(tmp_path), {"champion": "v1", "runs": []})
    assert p.parent == tmp_path and p.suffix == ".json"
    assert json.loads(p.read_text())["champion"] == "v1"
    q = ev.dump_transcripts(None, {"x": 1})  # temp default, no repo litter
    assert json.loads(q.read_text()) == {"x": 1}
