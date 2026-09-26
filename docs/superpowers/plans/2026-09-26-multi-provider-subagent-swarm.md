# Multi-Provider Subagent Swarm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `research_spar`'s fake per-family routing (notelab), and build an isolated, worktree-per-agent code-fix dispatcher ([redacted-private-project]) that races the user's real CLI fleet (opencode/muse/cline/kilo/kiro-cli) against one real bug, with nothing auto-merged.

**Architecture:** Two independent trust tiers sharing one fixed utility. `research/evals.py::run_adversary` (notelab) gets a `{cwd}` guard and a process-group kill on timeout — both components use it. Component A (`spar()`) calls it with **no write-trust flags**, since research never edits files. Component B (`dispatch_code_fix.py`, [redacted-private-project]) calls it with full-trust flags **only inside an isolated git worktree**, captures the diff, gates acceptance on tests passing, persists a report to disk, and records real outcomes into the existing `redacted_private_project.routing_monitor` ledger.

**Tech Stack:** Python 3.10+ stdlib (`subprocess`, `argparse`, `concurrent.futures`), `pytest`, existing `redacted_private_project.routing_monitor` ledger schema, `uv` for per-worktree env provisioning.

**Spec:** `docs/superpowers/specs/2026-09-26-multi-provider-subagent-swarm-design.md` (this repo) — reviewed by `/plan-eng-review` and `/plan-devex-review`, 11 findings, all resolved in the spec's "Revision after eng + devex review" section. This plan implements that revised design.

---

## File Structure

- **Modify** `notelab/src/notelab/research/evals.py` — add 4 `RunnerSpec`s to `KNOWN_RUNNERS` (research-only, no write trust); rewrite `run_adversary` with a guarded `{cwd}` substitution and a process-group kill on timeout.
- **Modify** `notelab/src/notelab/research/sparring.py` — add `_sources_from_adversary_output()`.
- **Modify** `notelab/src/notelab/_tools.py` — fix `spar()`'s `family_runner` to call `run_adversary` per family instead of the shared NotebookLM path; add `_notelab_repo_root()`.
- **Modify** `notelab/tests/test_evolve_and_evals.py` — update the family-count assertion; add `{cwd}`-guard and process-group-kill tests.
- **Create** `notelab/tests/test_sparring_adversary_sources.py` — tests for `_sources_from_adversary_output()`.
- **Create** `notelab/tests/test_tools_spar.py` — integration test proving `spar()` dispatches real per-family CLIs, not one shared path.
- **Create** `[redacted-private-project]/tests/test_dispatch_code_fix.py` — smoke tests for the new dispatcher, all `run_adversary`/`git`/`uv` calls mocked.
- **Create** `[redacted-private-project]/examples/dispatch_code_fix.py` — the worktree-isolated code-fix race dispatcher.
- **Create** `[redacted-private-project]/examples/prompts/gap2-semantic-fix.txt` — the exact task prompt for the first real run.

---

## Task 1: Component A — add the 4 missing research-only runners

**Files:**
- Modify: `src/notelab/research/evals.py:41-49` (`KNOWN_RUNNERS`)
- Test: `tests/test_evolve_and_evals.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_evolve_and_evals.py`, replacing the existing `test_registry_ships_all_six_families`:

```python
def test_registry_ships_all_ten_families():
    names = {r.name for r in detect_runners()}
    assert {"opencode", "copilot", "claude", "codex", "qoder", "qwen",
            "muse", "cline", "kilo", "kiro-cli"} <= names


def test_research_runners_never_carry_write_trust_flags():
    # Component A only ever asks a research question -- it must never be handed a
    # flag that grants an agent filesystem write trust (spec: "Revision after eng +
    # devex review", finding #2).
    dangerous = {"--yolo", "--auto", "-a", "--trust-all-tools"}
    for r in detect_runners():
        assert not (dangerous & set(r.args_template)), \
            f"{r.name} carries a write-trust flag in the research-only runner table"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/<you>/dev/notelab && .venv/bin/pytest tests/test_evolve_and_evals.py -k "ten_families or write_trust" -v`
Expected: FAIL — `test_registry_ships_all_ten_families` fails on the missing 4 names (assertion error, `KeyError`-shaped set diff); `test_research_runners_never_carry_write_trust_flags` passes vacuously today (no such flags exist yet) — that's fine, it's a regression guard for Step 3, not a red test.

- [ ] **Step 3: Add the 4 runners to `KNOWN_RUNNERS`**

In `src/notelab/research/evals.py`, replace the `KNOWN_RUNNERS` list (currently lines 41-49):

```python
KNOWN_RUNNERS: list[RunnerSpec] = [
    RunnerSpec("opencode", "opencode", ["run", "{prompt}"]),
    RunnerSpec("copilot", "copilot", ["-p", "{prompt}", "--allow-all-tools"]),
    RunnerSpec("claude", "claude", ["-p", "{prompt}"]),
    RunnerSpec("codex", "codex", ["exec", "{prompt}"]),
    # qoder (@qoder-ai/qodercli) and qwen (QwenLM/qwen-code): headless one-shot verified.
    RunnerSpec("qoder", "qoder", ["-p", "{prompt}"]),
    RunnerSpec("qwen", "qwen", ["{prompt}"]),
    # muse/cline/kilo/kiro-cli: research-only, deliberately NO auto-approve/trust flag.
    # Component A only ever asks a research question, never asks an agent to edit
    # anything, so it never needs write trust -- omitting these flags means each
    # tool's own DEFAULT (conservative) permission mode applies, and a headless
    # session with no one to approve a tool call fails that call safely rather than
    # silently granting it. See docs/superpowers/specs/2026-09-26-multi-provider-
    # subagent-swarm-design.md, "Revision after eng + devex review", finding #2.
    RunnerSpec("muse", "muse", ["exec", "{prompt}"]),
    RunnerSpec("cline", "cline", ["--auto-approve", "false", "{prompt}"]),
    RunnerSpec("kilo", "kilo", ["run", "{prompt}"]),
    RunnerSpec("kiro-cli", "kiro-cli", ["chat", "{prompt}", "--no-interactive"]),
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/<you>/dev/notelab && .venv/bin/pytest tests/test_evolve_and_evals.py -v`
Expected: PASS, all tests in the file green (including the two pre-existing skip-guard tests, unaffected by this change).

- [ ] **Step 5: Commit**

```bash
cd /Users/<you>/dev/notelab
git add src/notelab/research/evals.py tests/test_evolve_and_evals.py
git commit -m "feat(evals): add muse/cline/kilo/kiro-cli as research-only runners

No write-trust flags -- spar() only ever asks a research question, so
these never need filesystem write access. Component A half of the
multi-provider swarm design."
```

---

## Task 2: `run_adversary` — guard the `{cwd}` placeholder, don't crash

**Files:**
- Modify: `src/notelab/research/evals.py:79-100` (`run_adversary`)
- Test: `tests/test_evolve_and_evals.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_evolve_and_evals.py`:

```python
def test_template_with_cwd_placeholder_and_no_cwd_given_is_skipped():
    from notelab.research.evals import RunnerSpec
    spec = RunnerSpec("needs-cwd", "sh", ["-c", "{prompt}", "{cwd}"],
                      available=True, path="/bin/sh")
    res = run_adversary(spec, "echo hi")  # no cwd= passed
    assert res.available is True
    assert res.returncode is None          # never executed
    assert res.skipped_reason and "{cwd}" in res.skipped_reason


def test_cwd_placeholder_is_substituted_when_cwd_given(tmp_path):
    from notelab.research.evals import RunnerSpec
    marker = tmp_path / "cwd-seen.txt"
    spec = RunnerSpec("echo-cwd", "sh",
                      ["-c", f"pwd > {marker}; echo {{prompt}}"],
                      available=True, path="/bin/sh")
    res = run_adversary(spec, "hello", cwd=str(tmp_path))
    assert res.returncode == 0
    assert marker.read_text().strip() == str(tmp_path.resolve())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/<you>/dev/notelab && .venv/bin/pytest tests/test_evolve_and_evals.py -k cwd -v`
Expected: `test_template_with_cwd_placeholder_and_no_cwd_given_is_skipped` FAILS with `TypeError: replace() argument 'new' must be str, not None` (the crash bug the eng review found) or an `AssertionError` if it doesn't crash cleanly. `test_cwd_placeholder_is_substituted_when_cwd_given` FAILS because `{cwd}` is never substituted today (the literal string `{cwd}` ends up in the shell command).

- [ ] **Step 3: Rewrite `run_adversary`**

In `src/notelab/research/evals.py`, add `import os` and `import signal` to the top imports (alongside the existing `shutil`/`subprocess`), then replace `run_adversary` (currently lines 79-100):

```python
def run_adversary(spec: RunnerSpec, prompt: str, timeout: int = 300,
                  cwd: str | None = None) -> RunnerResult:
    """Invoke one adversarial agent CLI on `prompt`. Never raises — a missing or
    failing runner is a recorded result, not an exception, so an eval over several
    runners is robust to any one of them being absent or broken.

    On timeout, kills the CHILD'S WHOLE PROCESS GROUP (start_new_session=True +
    killpg), not just the direct child -- a CLI's own spawned grandchildren (browser
    automation, an LSP server) would otherwise survive undetected past the timeout."""
    if not spec.available or not spec.path:
        return RunnerResult(spec.name, available=False,
                            skipped_reason=f"{spec.bin} not on PATH")
    # Guard: a template with no {prompt} placeholder would silently run the agent
    # with NO task, producing a meaningless "success". Refuse it as a recorded skip
    # rather than emitting a false datapoint into the eval ledger.
    if not any("{prompt}" in a for a in spec.args_template):
        return RunnerResult(spec.name, available=True,
                            skipped_reason="args_template has no {prompt} placeholder")
    needs_cwd = any("{cwd}" in a for a in spec.args_template)
    if needs_cwd and not cwd:
        return RunnerResult(spec.name, available=True,
                            skipped_reason="args_template has {cwd} but no cwd was given")
    args = [spec.path]
    for a in spec.args_template:
        a = a.replace("{prompt}", prompt)
        if needs_cwd:
            a = a.replace("{cwd}", cwd)
        args.append(a)
    try:
        proc = subprocess.Popen(args, cwd=cwd, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
    except Exception as e:  # pragma: no cover - defensive
        return RunnerResult(spec.name, True, None, stderr=f"{type(e).__name__}: {e}")
    try:
        out, err = proc.communicate(timeout=timeout)
        return RunnerResult(spec.name, True, proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()  # reap the (now-dead) process, avoid a zombie
        return RunnerResult(spec.name, True, None, timed_out=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/<you>/dev/notelab && .venv/bin/pytest tests/test_evolve_and_evals.py -v`
Expected: PASS, all tests green — including the two PRE-EXISTING tests (`test_missing_runner_is_skipped_not_raised`, `test_template_without_prompt_placeholder_is_skipped_not_run`), which must still pass unchanged (regression check on the rewrite).

- [ ] **Step 5: Commit**

```bash
cd /Users/<you>/dev/notelab
git add src/notelab/research/evals.py tests/test_evolve_and_evals.py
git commit -m "fix(evals): guard {cwd} substitution in run_adversary, don't crash

A template containing {cwd} with no cwd= argument now returns a recorded
skip instead of raising TypeError -- the eng review's P1 crash-bug finding."
```

---

## Task 3: `run_adversary` — kill the whole process group on timeout

**Files:**
- Test: `tests/test_evolve_and_evals.py`

(The implementation for this shipped as part of Task 2's rewrite — `start_new_session=True` + `os.killpg` in the `except subprocess.TimeoutExpired` branch. This task adds the test that actually proves grandchildren die, which needs a real subprocess and a short sleep, so it's kept separate from Task 2's quick unit tests.)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_evolve_and_evals.py` (needs `import time` added to the file's existing imports):

```python
def test_timeout_kills_process_group_including_grandchildren(tmp_path):
    from notelab.research.evals import RunnerSpec
    # The child backgrounds a grandchild that touches `marker` 5s from now. A timeout
    # that only kills the direct child leaves this grandchild running; a real
    # process-group kill takes it down too, so `marker` must never appear.
    marker = tmp_path / "grandchild_alive"
    script = tmp_path / "spawn.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"(sleep 5; touch {marker}) &\n"
        "sleep 5\n"
    )
    script.chmod(0o755)
    spec = RunnerSpec("spawner", str(script), ["{prompt}"], available=True, path=str(script))

    res = run_adversary(spec, "unused", timeout=1)
    assert res.timed_out is True

    time.sleep(2)  # T+3s total; the grandchild only fires its marker at T+5s if alive
    assert not marker.exists(), "grandchild survived the timeout kill"
```

- [ ] **Step 2: Run test to verify it fails**

Temporarily revert `run_adversary` to use `subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd)` without `start_new_session=True`/`killpg` (i.e. don't do this — Task 2 already shipped the real fix). Since Task 2's implementation is already the fixed version, this test should PASS immediately after Task 2. Run it now to confirm:

Run: `cd /Users/<you>/dev/notelab && .venv/bin/pytest tests/test_evolve_and_evals.py -k grandchildren -v`
Expected: PASS (Task 2's rewrite already contains the fix — this step exists to give the process-group-kill behavior its own explicit, real-subprocess regression test, not to re-implement anything).

- [ ] **Step 3: Commit**

```bash
cd /Users/<you>/dev/notelab
git add tests/test_evolve_and_evals.py
git commit -m "test(evals): prove run_adversary's timeout kills grandchild processes

Explicit real-subprocess regression test for the process-group kill Task 2
shipped -- devex review finding #2 (orphaned processes past a timeout)."
```

---

## Task 4: `_sources_from_adversary_output()` — turn a CLI's raw answer into scorable sources

**Files:**
- Modify: `src/notelab/research/sparring.py`
- Create: `tests/test_sparring_adversary_sources.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_sparring_adversary_sources.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/<you>/dev/notelab && .venv/bin/pytest tests/test_sparring_adversary_sources.py -v`
Expected: FAIL — `ImportError: cannot import name '_sources_from_adversary_output' from 'notelab.research.sparring'`.

- [ ] **Step 3: Implement `_sources_from_adversary_output()`**

In `src/notelab/research/sparring.py`, add `import re` to the imports, add `from .evals import RunnerResult` (safe — `evals.py` imports nothing from `sparring.py`, no circular import), and add this function after the `FamilyRunner` type alias (after line 40):

```python
_URL_RE = re.compile(r"https?://\S+")


def _sources_from_adversary_output(result: RunnerResult, question: str) -> list[SourceFeatures]:
    """Turn one adversarial CLI's raw stdout into SourceFeatures score_sources() can
    rank. Real answers rarely come back as structured JSON -- extract cited URLs with
    a bounded content snippet around each; if the CLI answered in prose with no links
    at all, that's still a real (if weaker) outcome, not a non-event, so the whole
    stdout becomes one sourceless SourceFeatures rather than an empty list. A
    genuinely empty answer still yields an empty list -- there is nothing to score."""
    text = result.stdout or ""
    urls = _URL_RE.findall(text)
    if not urls:
        if not text.strip():
            return []
        return [SourceFeatures(id=f"{result.runner}-0", title=f"{result.runner} answer",
                               url="", content=text[:2000])]
    out = []
    for i, url in enumerate(urls):
        idx = text.find(url)
        start = max(0, idx - 200)
        end = min(len(text), idx + len(url) + 200)
        out.append(SourceFeatures(id=f"{result.runner}-{i}", title=f"{result.runner} source {i}",
                                  url=url, content=text[start:end]))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/<you>/dev/notelab && .venv/bin/pytest tests/test_sparring_adversary_sources.py -v`
Expected: PASS, all 4 tests green.

- [ ] **Step 5: Commit**

```bash
cd /Users/<you>/dev/notelab
git add src/notelab/research/sparring.py tests/test_sparring_adversary_sources.py
git commit -m "feat(sparring): extract scorable sources from a raw adversarial CLI answer"
```

---

## Task 5: fix `spar()` — real per-family dispatch, not the shared NotebookLM path

**Files:**
- Modify: `src/notelab/_tools.py:1-24` (imports), `:179-203` (`spar()`)
- Create: `tests/test_tools_spar.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tools_spar.py`:

```python
"""spar() must dispatch each family through its OWN CLI (run_adversary), not the
shared NotebookLM path -- fixes FIELD-NOTES.md gap #9. Offline: detect_runners and
run_adversary are both monkeypatched, no real CLI or NotebookLM call."""
import json

import notelab._tools as tools_mod
from notelab._tools import ToolLogic
from notelab.research.evals import RunnerResult, RunnerSpec


class _FakeStore:
    def record_sparring(self, result):
        return "sparring-1"


def test_spar_dispatches_each_family_through_its_own_cli(monkeypatch):
    specs = [
        RunnerSpec("opencode", "opencode", ["run", "{prompt}"], available=True, path="/x/opencode"),
        RunnerSpec("cline", "cline", ["--auto-approve", "false", "{prompt}"],
                  available=True, path="/x/cline"),
    ]
    monkeypatch.setattr(tools_mod, "detect_runners", lambda: specs)

    calls = []

    def fake_run_adversary(spec, prompt, timeout=180, cwd=None):
        calls.append(spec.name)
        return RunnerResult(spec.name, True, 0,
                            stdout=f"{spec.name} says: see https://example.com/{spec.name}",
                            stderr="")

    monkeypatch.setattr(tools_mod, "run_adversary", fake_run_adversary)

    tool = ToolLogic(adapter=object(), store=_FakeStore())
    out = json.loads(tool.spar("question", "prompt"))

    # both families were ACTUALLY dispatched -- this is the regression check for
    # gap #9 (every family previously routed through the identical NotebookLM path
    # regardless of which CLI was "detected")
    assert sorted(calls) == ["cline", "opencode"]

    by_family = {r["family"]: r for r in out["results"]}
    assert by_family["opencode"]["available"] is True
    assert by_family["cline"]["available"] is True
    urls = {f: r["report"]["compare"] if False else None for f, r in by_family.items()}
    # each family's OWN url shows up in its own report, not a shared/identical one
    opencode_dump = json.dumps(by_family["opencode"])
    cline_dump = json.dumps(by_family["cline"])
    assert "example.com/opencode" in opencode_dump
    assert "example.com/opencode" not in cline_dump
    assert "example.com/cline" in cline_dump


def test_spar_records_unavailable_family_without_calling_run_adversary(monkeypatch):
    specs = [RunnerSpec("kilo", "kilo", ["run", "{prompt}"], available=False, path=None)]
    monkeypatch.setattr(tools_mod, "detect_runners", lambda: specs)

    def fail_if_called(*a, **k):
        raise AssertionError("run_adversary must not be called for an unavailable family")

    monkeypatch.setattr(tools_mod, "run_adversary", fail_if_called)

    tool = ToolLogic(adapter=object(), store=_FakeStore())
    out = json.loads(tool.spar("q", "p"))
    assert out["results"][0]["available"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/<you>/dev/notelab && .venv/bin/pytest tests/test_tools_spar.py -v`
Expected: FAIL — `calls` stays empty (the current `family_runner` calls `self.adapter.research_run(...)`, which `object()` as the fake adapter doesn't even implement, so this actually errors with `AttributeError: 'object' object has no attribute 'research_run'` — either way, red).

- [ ] **Step 3: Fix `spar()`**

In `src/notelab/_tools.py`, add `from pathlib import Path` to the top imports (after `import json`), and change the import line (currently line 23) to also pull in `_sources_from_adversary_output`:

```python
from .research.quality import QualityWeights, score_sources
from .research.sparring import _sources_from_adversary_output, run_sparring
```

Then add this helper near the top of the file, after `_weights_from` (after line 38):

```python
def _notelab_repo_root() -> str:
    return str(Path(__file__).resolve().parents[2])
```

Then replace the `spar()` method body (currently lines 179-203):

```python
    def spar(self, question: str, prompt: str, families: list | None = None) -> str:
        """M4: run one prompt across model families over the same question, score each,
        record per-family attribution, and return the ranked compare view.

        A family participates only if its adversarial CLI is installed (detected at
        runtime); unavailable families are recorded as such. Each available family's
        research goes through ITS OWN CLI via run_adversary -- not a shared
        NotebookLM path. Fixes FIELD-NOTES.md gap #9: previously every family routed
        through the identical `self.adapter.research_run(...)` call regardless of
        which CLI was detected, so the "which family finds the best sources" ranking
        never actually compared model families at all."""
        specs = {s.name: s for s in detect_runners()}
        fams = families or list(specs.keys())

        def family_runner(family: str, p: str):
            spec = specs.get(family)
            if spec is None or not spec.available:
                return [], {"available": False, "error": f"{family} not installed"}
            result = run_adversary(spec, p, timeout=180, cwd=_notelab_repo_root())
            if result.timed_out:
                return [], {"available": True, "error": "timed out"}
            sources = _sources_from_adversary_output(result, question=p)
            return sources, {"available": True,
                             "error": result.stderr[:300] if result.returncode else None}

        session = run_sparring(question, prompt, fams, runner=family_runner)
        result = session.as_dict()
        result["sparring_id"] = self.store.record_sparring(result)
        return json.dumps(result, indent=2)
```

Remove the now-redundant local imports that used to sit at the top of the old `spar()` body (`from .research.evals import detect_runners` and `from .research.sparring import run_sparring`) — both names are now imported at module level (`detect_runners`/`run_adversary` already were, on the existing line 21; `run_sparring` is now added alongside `_sources_from_adversary_output` above).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/<you>/dev/notelab && .venv/bin/pytest tests/test_tools_spar.py -v`
Expected: PASS, both tests green.

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `cd /Users/<you>/dev/notelab && .venv/bin/pytest -q`
Expected: PASS, all tests green (126+ existing tests plus the ones added in Tasks 1-5).

- [ ] **Step 6: Commit**

```bash
cd /Users/<you>/dev/notelab
git add src/notelab/_tools.py tests/test_tools_spar.py
git commit -m "fix(_tools): spar() dispatches real per-family CLIs, fixes gap #9

family_runner previously ignored detect_runners()'s availability check and
routed every family through the identical NotebookLM path regardless of
which CLI was actually installed -- the family_leaderboard ranking never
compared model families at all. Now each available family's research goes
through run_adversary against its OWN CLI, research-only (no write trust,
see Task 1)."
```

---

## Task 6: `dispatch_code_fix.py` — write the test suite first

**Files:**
- Create: `[redacted-private-project]/tests/test_dispatch_code_fix.py`

- [ ] **Step 1: Write the full test file**

Create `[redacted-private-project]/tests/test_dispatch_code_fix.py`:

```python
"""Smoke tests for dispatch_code_fix.py -- git/subprocess/run_adversary are all
mocked, no real CLI invocation and no real cloud spend. See docs/superpowers/specs/
2026-09-26-multi-provider-subagent-swarm-design.md (notelab) for the design."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))
import dispatch_code_fix as dcf  # noqa: E402


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_worktree_exists_detects_existing_branch(monkeypatch, tmp_path):
    def fake_git(args, cwd=None, timeout=30):
        if args[:2] == ["worktree", "list"]:
            return _completed(stdout="worktree /x\nbranch refs/heads/swarm/opencode/t1\n")
        return _completed(stdout="")
    monkeypatch.setattr(dcf, "_git", fake_git)
    assert dcf.worktree_exists(tmp_path, "opencode", "t1") is True


def test_worktree_exists_false_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(dcf, "_git", lambda args, cwd=None, timeout=30: _completed(stdout=""))
    assert dcf.worktree_exists(tmp_path, "opencode", "t1") is False


def test_dispatch_one_records_timeout(tmp_path):
    spec = SimpleNamespace(name="kilo", available=True)

    def fake_run_adversary(spec, prompt, timeout=180, cwd=None):
        return SimpleNamespace(stdout="", stderr="", timed_out=True, skipped_reason=None)

    out = dcf.dispatch_one(spec, fake_run_adversary, "do the fix", tmp_path, 5, 30, 30)
    assert out.status == "timed_out"
    assert out.accepted() is False


def test_dispatch_one_empty_diff_is_not_an_error(monkeypatch, tmp_path):
    spec = SimpleNamespace(name="cline", available=True)

    def fake_run_adversary(spec, prompt, timeout=180, cwd=None):
        return SimpleNamespace(stdout="looked, changed nothing", stderr="",
                               timed_out=False, skipped_reason=None)

    monkeypatch.setattr(dcf, "capture_diff", lambda wt: "")
    out = dcf.dispatch_one(spec, fake_run_adversary, "do the fix", tmp_path, 5, 30, 30)
    assert out.status == "dispatched"
    assert out.diff == ""
    assert out.test_status == "not_run"
    assert out.accepted() is False


def test_dispatch_one_env_provision_failure_is_could_not_run_not_failed(monkeypatch, tmp_path):
    spec = SimpleNamespace(name="opencode", available=True)

    def fake_run_adversary(spec, prompt, timeout=180, cwd=None):
        return SimpleNamespace(stdout="changed a file", stderr="",
                               timed_out=False, skipped_reason=None)

    monkeypatch.setattr(dcf, "capture_diff", lambda wt: "diff --git a/x b/x\n+real change\n")
    monkeypatch.setattr(dcf, "provision_env",
                        lambda wt, timeout=120: (False, "uv sync failed: no lockfile"))
    out = dcf.dispatch_one(spec, fake_run_adversary, "do the fix", tmp_path, 5, 30, 30)
    assert out.test_status == "could_not_run"
    assert out.env_status == "provision_failed"
    assert out.accepted() is False  # never misattributed as a code-quality failure


def test_dispatch_one_passing_tests_is_accepted(monkeypatch, tmp_path):
    spec = SimpleNamespace(name="opencode", available=True)

    def fake_run_adversary(spec, prompt, timeout=180, cwd=None):
        return SimpleNamespace(stdout="fixed it", stderr="", timed_out=False, skipped_reason=None)

    monkeypatch.setattr(dcf, "capture_diff", lambda wt: "diff --git a/x b/x\n+fix\n")
    monkeypatch.setattr(dcf, "provision_env", lambda wt, timeout=120: (True, ""))
    monkeypatch.setattr(dcf, "run_tests", lambda wt, timeout=300: "passed")
    out = dcf.dispatch_one(spec, fake_run_adversary, "do the fix", tmp_path, 5, 30, 30)
    assert out.test_status == "passed"
    assert out.accepted() is True


def test_dispatch_one_unavailable_agent_is_error_not_dispatched(tmp_path):
    spec = SimpleNamespace(name="ghost", available=False)

    def fail_if_called(*a, **k):
        raise AssertionError("run_adversary must not be called for an unavailable agent")

    out = dcf.dispatch_one(spec, fail_if_called, "do the fix", tmp_path, 5, 30, 30)
    assert out.status == "error"
    assert out.accepted() is False


def test_oracle_best_model_picks_fastest_passing_agent():
    a = dcf.AgentOutcome(agent="fast", status="dispatched", diff="+x", test_status="passed", elapsed_ms=100)
    b = dcf.AgentOutcome(agent="slow", status="dispatched", diff="+y", test_status="passed", elapsed_ms=500)
    c = dcf.AgentOutcome(agent="failed", status="dispatched", diff="+z", test_status="failed", elapsed_ms=50)
    assert dcf.oracle_best_model([a, b, c]) == "fast"


def test_oracle_best_model_none_when_nobody_passes():
    a = dcf.AgentOutcome(agent="a", status="timed_out")
    assert dcf.oracle_best_model([a]) == "none"


def test_write_report_persists_to_disk(tmp_path):
    outcomes = [dcf.AgentOutcome(agent="opencode", status="dispatched", diff="+x",
                                 test_status="passed", elapsed_ms=42.0)]
    run_dir = dcf.write_report(tmp_path, "t1", outcomes)
    assert (run_dir / "report.json").exists()
    assert (run_dir / "report.md").exists()
    data = json.loads((run_dir / "report.json").read_text())
    assert data["agents"][0]["agent"] == "opencode"
    assert data["agents"][0]["accepted"] is True


def test_record_ledger_writes_a_step_per_agent(tmp_path):
    outcomes = [
        dcf.AgentOutcome(agent="opencode", status="dispatched", diff="+x",
                         test_status="passed", elapsed_ms=100.0),
        dcf.AgentOutcome(agent="cline", status="timed_out", elapsed_ms=0.0),
    ]
    db = tmp_path / "swarm.db"
    dcf.record_ledger(db, "t1", outcomes)
    assert db.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/<you>/dev/[redacted-private-project] && .venv/bin/pytest tests/test_dispatch_code_fix.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dispatch_code_fix'` (the script doesn't exist yet; Task 7 creates it).

- [ ] **Step 3: Commit the test file**

```bash
cd /Users/<you>/dev/[redacted-private-project]
git add tests/test_dispatch_code_fix.py
git commit -m "test(swarm): failing smoke tests for dispatch_code_fix.py, pre-implementation"
```

---

## Task 7: `dispatch_code_fix.py` — the implementation

**Files:**
- Create: `[redacted-private-project]/examples/dispatch_code_fix.py`

- [ ] **Step 1: Write the implementation**

Create `[redacted-private-project]/examples/dispatch_code_fix.py`:

```python
#!/usr/bin/env python3
"""
dispatch_code_fix.py -- race a real code-fix task across the swarm's working CLI
fleet, each agent in its own isolated git worktree, nothing auto-merged.

See docs/superpowers/specs/2026-09-26-multi-provider-subagent-swarm-design.md
(sibling notelab repo) for the full design, and the eng + devex review passes that
design addresses.

Run:
    python3 examples/dispatch_code_fix.py \\
        --target-repo ../notelab --task-slug gap2-semantic-fix \\
        --prompt-file examples/prompts/gap2-semantic-fix.txt

Cheap single-agent first try:
    python3 examples/dispatch_code_fix.py --agents opencode ...   # (same other args)

Clean up a run's worktrees + branches:
    python3 examples/dispatch_code_fix.py --cleanup \\
        --target-repo ../notelab --task-slug gap2-semantic-fix
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from redacted_private_project.routing_monitor.ledger.schema import (  # noqa: E402
    REGRET_ROUTE_EXACT,
    REGRET_UNDER_ROUTE,
    REGRET_UNKNOWN,
    Attribution,
    RoutingMetadata,
    TokenUsage,
)
from redacted_private_project.routing_monitor.ledger.store import LedgerStore  # noqa: E402
from redacted_private_project.routing_monitor.pricing import ModelPrice, Pricing  # noqa: E402
from redacted_private_project.routing_monitor.session import Session  # noqa: E402

DEFAULT_TIMEOUT = 180
DEFAULT_DB = Path.home() / ".local/state/[redacted-private-project]/swarm-runs.db"


def _load_code_fix_runners(target_repo: Path):
    """Import RunnerSpec/run_adversary from the TARGET repo's own source -- the
    --target-repo the caller already gave us is the one and only coupling point, not
    a separate hidden relative-path assumption (devex review finding #5)."""
    src = target_repo / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from notelab.research.evals import RunnerSpec, run_adversary  # noqa: F401

    # This is the ONLY place full-trust flags appear -- deliberately a separate
    # table from notelab's own research-only KNOWN_RUNNERS (Task 1). Worktree
    # isolation exists specifically to bound these.
    templates = [
        RunnerSpec("opencode", "opencode", ["run", "--dir", "{cwd}", "{prompt}"]),
        RunnerSpec("muse", "muse", ["exec", "--workspace", "{cwd}", "--yolo", "{prompt}"]),
        RunnerSpec("cline", "cline", ["--cwd", "{cwd}", "{prompt}"]),
        RunnerSpec("kilo", "kilo", ["run", "--dir", "{cwd}", "--auto", "{prompt}"]),
        RunnerSpec("kiro-cli", "kiro-cli", ["chat", "{prompt}", "--no-interactive", "-a"]),
    ]
    detected = []
    for r in templates:
        path = shutil.which(r.bin)
        detected.append(RunnerSpec(r.name, r.bin, list(r.args_template),
                                   available=path is not None, path=path))
    return detected, run_adversary


def branch_name(agent: str, task_slug: str) -> str:
    return f"swarm/{agent}/{task_slug}"


def worktree_path(target_repo: Path, task_slug: str, agent: str) -> Path:
    return target_repo.parent / ".swarm-worktrees" / task_slug / agent


def _git(args: list, cwd: Path | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def worktree_exists(target_repo: Path, agent: str, task_slug: str) -> bool:
    branch = branch_name(agent, task_slug)
    r = _git(["worktree", "list", "--porcelain"], cwd=target_repo)
    if f"branch refs/heads/{branch}" in r.stdout:
        return True
    r = _git(["branch", "--list", branch], cwd=target_repo)
    return bool(r.stdout.strip())


def create_worktree(target_repo: Path, agent: str, task_slug: str) -> Path:
    wt = worktree_path(target_repo, task_slug, agent)
    wt.parent.mkdir(parents=True, exist_ok=True)
    branch = branch_name(agent, task_slug)
    r = _git(["worktree", "add", "-b", branch, str(wt), "HEAD"], cwd=target_repo, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"git worktree add failed for {agent}: {r.stderr.strip()}")
    return wt


def remove_worktree(target_repo: Path, agent: str, task_slug: str) -> None:
    wt = worktree_path(target_repo, task_slug, agent)
    _git(["worktree", "remove", "--force", str(wt)], cwd=target_repo, timeout=30)
    _git(["branch", "-D", branch_name(agent, task_slug)], cwd=target_repo, timeout=30)


def cleanup(target_repo: Path, task_slug: str, agents: list) -> None:
    for agent in agents:
        remove_worktree(target_repo, agent, task_slug)
        print(f"  removed worktree + branch for {agent}")


@dataclass
class AgentOutcome:
    agent: str
    status: str = "pending"          # pending | dispatched | timed_out | error
    env_status: str = "pending"      # pending | provisioned | provision_failed
    test_status: str = "not_run"     # not_run | passed | failed | could_not_run
    diff: str = ""
    elapsed_ms: float = 0.0
    stdout_tail: str = ""
    stderr_tail: str = ""
    error: str = ""

    def accepted(self) -> bool:
        return (self.status == "dispatched" and bool(self.diff.strip())
                and self.test_status == "passed")

    def as_dict(self) -> dict:
        return {
            "agent": self.agent, "status": self.status, "env_status": self.env_status,
            "test_status": self.test_status, "elapsed_ms": round(self.elapsed_ms, 1),
            "diff_bytes": len(self.diff), "accepted": self.accepted(),
            "stdout_tail": self.stdout_tail, "stderr_tail": self.stderr_tail,
            "error": self.error,
        }


def provision_env(worktree: Path, timeout: int = 120):
    r = subprocess.run(["uv", "sync"], cwd=worktree, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout)[-500:]
    return True, ""


def capture_diff(worktree: Path) -> str:
    # -N stages new files' PRESENCE (not content) so untracked additions show up in
    # the diff too, not just edits to already-tracked files.
    subprocess.run(["git", "add", "-A", "-N", "."], cwd=worktree,
                   capture_output=True, text=True, timeout=30)
    r = subprocess.run(["git", "diff"], cwd=worktree, capture_output=True, text=True, timeout=30)
    return r.stdout


def run_tests(worktree: Path, timeout: int = 300) -> str:
    r = subprocess.run(["uv", "run", "pytest", "-q"], cwd=worktree,
                       capture_output=True, text=True, timeout=timeout)
    return "passed" if r.returncode == 0 else "failed"


def dispatch_one(spec, run_adversary, prompt: str, worktree: Path, timeout: int,
                 env_timeout: int, test_timeout: int) -> AgentOutcome:
    out = AgentOutcome(agent=spec.name)
    if not spec.available:
        out.status = "error"
        out.error = f"{spec.name} not installed"
        return out

    t0 = time.monotonic()
    result = run_adversary(spec, prompt, timeout=timeout, cwd=str(worktree))
    out.elapsed_ms = (time.monotonic() - t0) * 1000
    out.stdout_tail = (result.stdout or "")[-2000:]
    out.stderr_tail = (result.stderr or "")[-500:]

    if result.timed_out:
        out.status = "timed_out"
        return out
    if result.skipped_reason:
        out.status = "error"
        out.error = result.skipped_reason
        return out

    out.status = "dispatched"
    out.diff = capture_diff(worktree)
    if not out.diff.strip():
        out.test_status = "not_run"
        return out

    provisioned, provision_err = provision_env(worktree, timeout=env_timeout)
    if not provisioned:
        out.env_status = "provision_failed"
        out.test_status = "could_not_run"
        out.error = provision_err
        return out
    out.env_status = "provisioned"
    out.test_status = run_tests(worktree, timeout=test_timeout)
    return out


def write_report(redacted_private_project_root: Path, task_slug: str, outcomes: list) -> Path:
    run_dir = redacted_private_project_root / ".swarm-runs" / task_slug
    run_dir.mkdir(parents=True, exist_ok=True)
    data = {"task_slug": task_slug, "agents": [o.as_dict() for o in outcomes]}
    (run_dir / "report.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    lines = [f"# Swarm run: {task_slug}", ""]
    for o in outcomes:
        mark = "PASS" if o.accepted() else "FAIL"
        lines.append(f"## [{mark}] {o.agent} -- {o.status} / tests={o.test_status} "
                    f"({o.elapsed_ms:.0f}ms)")
        if o.error:
            lines.append(f"error: {o.error}")
        if o.diff.strip():
            lines.append("```diff")
            lines.append(o.diff[:4000])
            lines.append("```")
        lines.append("")
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return run_dir


def oracle_best_model(outcomes: list) -> str:
    passers = [o for o in outcomes if o.accepted()]
    if not passers:
        return "none"
    return min(passers, key=lambda o: o.elapsed_ms).agent


def record_ledger(db_path: Path, task_slug: str, outcomes: list) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    zero = ModelPrice(Decimal("0"), Decimal("0"))
    store = LedgerStore(db_path=db_path, stamp_git=True)
    session = Session(f"swarm-{task_slug}", Pricing.load(default_price=zero), store)
    best = oracle_best_model(outcomes)
    for o in outcomes:
        if o.status == "error":
            regret = REGRET_UNKNOWN
        elif o.accepted():
            regret = REGRET_ROUTE_EXACT
        elif o.status == "dispatched":
            regret = REGRET_UNDER_ROUTE
        else:
            regret = REGRET_UNKNOWN
        attribution = Attribution(
            was_from_router=True,
            oracle_best_model=best,
            outcome_accepted=o.accepted(),
            accepted_by="tests_green" if o.accepted() else "",
            routed_latency_ms=o.elapsed_ms,
            regret=regret,
        )
        session.record_step(
            routing=RoutingMetadata(
                strategy_name="swarm-code-fix-race", requested_model="self",
                routed_model=o.agent, counterfactual_baseline_model="self",
                provider_name=o.agent, decision_reason=f"swarm race: {task_slug}",
            ),
            usage=TokenUsage(input_tokens=0, output_tokens=0),
            attribution=attribution,
            action_name=f"code_fix: {task_slug}",
            execution_latency_ms=o.elapsed_ms,
            selected_ans_correct=o.accepted() if o.status != "error" else None,
            tags=["swarm_race", task_slug],
        )
    store.close()


def main(argv: list | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target-repo", required=True, type=Path)
    p.add_argument("--task-slug", required=True)
    p.add_argument("--prompt-file", type=Path)
    p.add_argument("--agents", default="", help="comma-separated subset, default: all")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    p.add_argument("--cleanup", action="store_true")
    p.add_argument("--db", default=str(DEFAULT_DB))
    args = p.parse_args(argv)

    target_repo = args.target_repo.resolve()
    all_specs, run_adversary = _load_code_fix_runners(target_repo)
    wanted = set(args.agents.split(",")) if args.agents else None
    specs = [s for s in all_specs if wanted is None or s.name in wanted]
    agent_names = [s.name for s in specs]

    if args.cleanup:
        cleanup(target_repo, args.task_slug, agent_names)
        return 0

    if not args.prompt_file:
        print("error: --prompt-file is required unless --cleanup", file=sys.stderr)
        return 1
    prompt = args.prompt_file.read_text(encoding="utf-8")

    worktrees = {}
    for spec in specs:
        if worktree_exists(target_repo, spec.name, args.task_slug):
            if not args.resume:
                print(f"error: worktree for {spec.name} already exists from a prior "
                     f"run -- remove it or pass --resume", file=sys.stderr)
                return 1
            worktrees[spec.name] = worktree_path(target_repo, args.task_slug, spec.name)
        else:
            worktrees[spec.name] = create_worktree(target_repo, spec.name, args.task_slug)

    outcomes = []
    with ThreadPoolExecutor(max_workers=len(specs) or 1) as pool:
        futures = {
            pool.submit(dispatch_one, spec, run_adversary, prompt, worktrees[spec.name],
                       args.timeout, 120, 300): spec
            for spec in specs
        }
        for fut in as_completed(futures):
            outcomes.append(fut.result())

    redacted_private_project_root = Path(__file__).resolve().parent.parent
    report_dir = write_report(redacted_private_project_root, args.task_slug, outcomes)
    record_ledger(Path(args.db), args.task_slug, outcomes)

    print(f"\nSwarm race complete -- report at {report_dir}")
    for o in sorted(outcomes, key=lambda x: x.agent):
        mark = "PASS" if o.accepted() else "FAIL"
        print(f"  [{mark}] {o.agent:10s} status={o.status:10s} tests={o.test_status:14s} "
             f"{o.elapsed_ms:8.0f}ms")
    print(f"  oracle_best_model: {oracle_best_model(outcomes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run Task 6's test suite to verify it passes**

Run: `cd /Users/<you>/dev/[redacted-private-project] && .venv/bin/pytest tests/test_dispatch_code_fix.py -v`
Expected: PASS, all 11 tests green.

- [ ] **Step 3: Run the full [redacted-private-project] test suite to check for regressions**

Run: `cd /Users/<you>/dev/[redacted-private-project] && .venv/bin/pytest -q`
Expected: PASS, all tests green (140+ existing tests plus the 11 new ones).

- [ ] **Step 4: Commit**

```bash
cd /Users/<you>/dev/[redacted-private-project]
chmod +x examples/dispatch_code_fix.py
git add examples/dispatch_code_fix.py
git commit -m "feat(swarm): dispatch_code_fix.py -- isolated multi-provider code-fix races

Worktree-per-agent isolation, sequential worktree creation + concurrent
dispatch, per-worktree uv sync before pytest (distinguishing environment
failures from real test failures), a persisted .swarm-runs/<slug>/report,
and real ledger recording with a second-pass oracle_best_model computed
from actual cross-agent comparison. Nothing auto-merges."
```

---

## Task 8: the first real run — race gap #2 across all 5 agents

**Files:**
- Create: `[redacted-private-project]/examples/prompts/gap2-semantic-fix.txt`

- [ ] **Step 1: Write the exact task prompt**

Create `[redacted-private-project]/examples/prompts/gap2-semantic-fix.txt` (verbatim from the spec's "First real run" section):

```
In src/notelab/research/semantic.py, before calling the Mistral embeddings
endpoint, resolve MISTRAL_EMBED_MODEL against a live GET
https://api.mistral.ai/v1/models call and raise a clear, distinct error if
the configured model id isn't in the returned list -- distinguishing
"model not found" from an auth/network failure, rather than only
discovering a stale id via a possibly-misleading HTTP error on the
embeddings call itself. Add a test using a mocked urllib response (no real
network/API key needed to run it). Keep the existing contract:
SemanticExpansionError on any failure, never a silent empty result.
```

- [ ] **Step 2: Smoke-test `kilo --dir` before the real run**

The eng review flagged `kilo run --dir <PATH>`'s help text as ambiguous ("path on remote server if attaching") for the local one-shot case. Confirm it actually scopes to the given directory rather than the process cwd:

Run:
```bash
mkdir -p /tmp/kilo-dir-test && cd /tmp/kilo-dir-test && echo "marker" > MARKER.txt
cd / && kilo run --dir /tmp/kilo-dir-test "list the files in the current directory"
```
Expected: the response mentions `MARKER.txt` (confirms `--dir` scopes kilo to that directory, not wherever the parent shell's cwd happened to be). If it does NOT, note this in the run's report and consider dropping `kilo` from the first live race rather than trusting an unverified flag against real cloud credits.

- [ ] **Step 3: Single-agent cheap first try**

Run:
```bash
cd /Users/<you>/dev/[redacted-private-project]
.venv/bin/python examples/dispatch_code_fix.py \
    --target-repo ../notelab --task-slug gap2-semantic-fix \
    --prompt-file examples/prompts/gap2-semantic-fix.txt --agents opencode
```
Expected: exits 0, prints one PASS/FAIL line for `opencode`, writes `.swarm-runs/gap2-semantic-fix/report.{md,json}`. Inspect the report before committing to a full 5-agent run — confirm the worktree mechanics, diff capture, and test gating all behaved as designed against one real agent first (cheap, per Pass 1 of the devex review).

- [ ] **Step 4: Clean up the single-agent trial, then run all 5**

```bash
.venv/bin/python examples/dispatch_code_fix.py --cleanup \
    --target-repo ../notelab --task-slug gap2-semantic-fix

.venv/bin/python examples/dispatch_code_fix.py \
    --target-repo ../notelab --task-slug gap2-semantic-fix \
    --prompt-file examples/prompts/gap2-semantic-fix.txt
```
Expected: exits 0, prints a PASS/FAIL line per agent (opencode, muse, cline, kilo, kiro-cli), reports `oracle_best_model`.

- [ ] **Step 5: Review the results, recommend a merge (or not)**

Read `.swarm-runs/gap2-semantic-fix/report.md`. For any agent marked PASS, inspect its actual diff in the worktree (`git -C <worktree> diff`) before recommending it for merge — an agent passing `pytest -q` is the objective bar, not a substitute for reading the diff. Report back: which (if any) diff should merge to `notelab`'s `main`, and why. Nothing merges automatically — this is a judgment call made from real results (spec's Open Question #2), not decided in advance.

- [ ] **Step 6: Clean up all 5 worktrees once review is done**

```bash
cd /Users/<you>/dev/[redacted-private-project]
.venv/bin/python examples/dispatch_code_fix.py --cleanup \
    --target-repo ../notelab --task-slug gap2-semantic-fix
```

- [ ] **Step 7: Commit the prompt file**

```bash
cd /Users/<you>/dev/[redacted-private-project]
git add examples/prompts/gap2-semantic-fix.txt
git commit -m "docs(swarm): add the gap #2 race prompt used for the first real run"
```

---

## Self-Review Notes

- **Spec coverage:** every numbered item in "Revision after eng + devex review" maps to a task above — eng #1/#2 → Tasks 1+2 (write-trust removal + `{cwd}` guard); eng #3 → Task 8 Step 2; eng #4 → Task 7's sequential-create/concurrent-dispatch design; devex #1 → Task 7 (pre-check/`--resume`/`--cleanup`); devex #2 → Task 3; devex #3 → Task 7 (`write_report`); devex #4 → Task 7 (`provision_env`, `could_not_run` vs `failed`); devex #5 → Task 7 (`--target-repo`); devex #6 → Task 7 (`oracle_best_model` second pass); devex #7 → Task 1 (KNOWN_RUNNERS stays `{cwd}`-free, so its docstring promise is never broken).
- **Placeholder scan:** none found — every step has real, complete code or an exact command with expected output.
- **Type consistency:** `AgentOutcome.accepted()` used identically in `write_report`, `oracle_best_model`, and `record_ledger`; `RunnerSpec`/`RunnerResult` field names (`available`, `path`, `args_template`, `timed_out`, `skipped_reason`, `stdout`, `stderr`, `returncode`) match `evals.py`'s actual dataclasses throughout every task that touches them.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-26-multi-provider-subagent-swarm.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
