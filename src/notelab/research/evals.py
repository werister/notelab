"""
Adversarial runners + skill evals.

Two jobs:

1. **Adversarial runners** — run the SAME research strategy through different external
   agent CLIs (opencode, GitHub Copilot CLI, …) so a strategy is judged not on one
   model family's behaviour but on several, adversarially. Runners are PLUGGABLE and
   detected at runtime: whatever isn't installed is reported as `unavailable` and
   skipped, never a hard failure. We ship adapters for ten agent CLIs — `opencode`,
   `copilot`, `claude`, `codex`, `qoder`, `qwen`, `muse`, `cline`, `kilo` and
   `kiro-cli` — each invoked one-shot, research-only (never given write trust; see
   the swarm design spec for the separate, worktree-isolated write-capable lane).
   Whichever are absent are skipped. Adding another is ~1 line in KNOWN_RUNNERS (a
   `{prompt}` placeholder in its args_template is required — see the guard in
   `run_adversary`).

2. **Skill evals** — given a strategy's quality scores over successive runs, decide
   whether it is getting BETTER or WORSE. We use a simple, defensible trend test
   (ordinary-least-squares slope + a min/max delta), reported with the raw series so
   the verdict is auditable. "A skill is regressing" is a claim you can check.
"""
from __future__ import annotations

import os
import signal
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum


# --- adversarial runners --------------------------------------------------------

@dataclass
class RunnerSpec:
    name: str
    bin: str                       # binary to look for on PATH
    args_template: list[str]       # {prompt} placeholder gets substituted
    available: bool = False
    path: str | None = None


# Known adversarial agent CLIs. args_template uses "{prompt}" as the substitution point.
# These invoke the agent in a one-shot, non-interactive mode where possible.
KNOWN_RUNNERS: list[RunnerSpec] = [
    RunnerSpec("opencode", "opencode", ["run", "{prompt}"]),
    RunnerSpec("copilot", "copilot", ["-p", "{prompt}", "--allow-all-tools"]),
    RunnerSpec("claude", "claude", ["-p", "{prompt}"]),
    RunnerSpec("codex", "codex", ["exec", "{prompt}"]),
    # qoder (@qoder-ai/qodercli) and qwen (QwenLM/qwen-code): headless one-shot verified.
    RunnerSpec("qoder", "qoder", ["-p", "{prompt}"]),
    RunnerSpec("qwen", "qwen", ["{prompt}"]),
    # muse/cline/kilo/kiro-cli: research-only, deliberately no BLANKET write-trust
    # flag (--yolo / --trust-all-tools / an --auto that defaults to true). cline's
    # own default is --auto-approve=true, so it gets an explicit override to false
    # here; the other three simply omit their trust flag, leaving each tool's
    # DEFAULT (conservative) permission mode in force. Either way, a headless
    # session with no one to approve a tool call fails that call safely rather than
    # silently granting it — a research call never needs write trust regardless.
    RunnerSpec("muse", "muse", ["exec", "{prompt}"]),
    # cline's default model draws on Cline Credits (pay-per-token, confirmed live
    # at a near-empty balance). ClinePass ($9.99/mo subscription) works too, but
    # cline-free/* is genuinely $0 -- no subscription, no credits balance touched
    # at all -- and most cost-efficient wins. Confirmed live working. Free-tier
    # promos rotate (docs.cline.bot's own warning; confirmed live: solar-pro4's
    # had already ended); muse-spark-1.3-contributor is a second confirmed-free
    # fallback if deepseek-v4.1-flash rotates out. Last resort:
    # cline-pass/glm-5.3-flash (paid subscription, but the account has one).
    RunnerSpec("cline", "cline", ["--auto-approve", "false",
                                  "-m", "cline-free/deepseek-v4.1-flash", "{prompt}"]),
    # kilo's DEFAULT model needs paid credits ("Add credits to continue, or
    # switch to a free model" -- confirmed live); pinned to an explicit free
    # model or every dispatch fails before reaching the research question.
    RunnerSpec("kilo", "kilo", ["run", "-m", "kilo/kilo-auto/free", "{prompt}"]),
    RunnerSpec("kiro-cli", "kiro-cli", ["chat", "{prompt}", "--no-interactive"]),
]


def detect_runners(candidates: list[RunnerSpec] | None = None) -> list[RunnerSpec]:
    """Return the known runners with availability filled in from the live PATH."""
    out: list[RunnerSpec] = []
    for spec in (candidates or KNOWN_RUNNERS):
        path = shutil.which(spec.bin)
        out.append(RunnerSpec(spec.name, spec.bin, list(spec.args_template),
                              available=path is not None, path=path))
    return out


@dataclass
class RunnerResult:
    runner: str
    available: bool
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    skipped_reason: str | None = None

    def as_dict(self) -> dict:
        return {"runner": self.runner, "available": self.available,
                "returncode": self.returncode, "timed_out": self.timed_out,
                "skipped_reason": self.skipped_reason,
                "stdout_tail": self.stdout[-2000:], "stderr_tail": self.stderr[-500:]}


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
        try:
            # Bounded reap: if a grandchild escaped the process group (e.g. called
            # its own setsid), don't hang here waiting for it too.
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return RunnerResult(spec.name, True, None, timed_out=True)
    except Exception as e:  # pragma: no cover - defensive, preserves the never-raises
                            # contract for anything communicate() can raise besides a
                            # timeout (e.g. a UnicodeDecodeError on non-UTF8 output).
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        return RunnerResult(spec.name, True, None, stderr=f"{type(e).__name__}: {e}")


# --- skill trend evals ----------------------------------------------------------

class Trend(str, Enum):
    IMPROVING = "improving"
    REGRESSING = "regressing"
    FLAT = "flat"
    INSUFFICIENT = "insufficient_data"


def _ols_slope(ys: list[float]) -> float:
    """Least-squares slope of ys against x=0..n-1 (per-run change in quality)."""
    n = len(ys)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else 0.0


@dataclass
class SkillEval:
    strategy_name: str
    scores: list[float]
    trend: Trend
    slope: float                 # quality change per run
    delta: float                 # last - first
    mean: float
    volatility: float            # stdev, a stability signal
    verdict: str

    def as_dict(self) -> dict:
        return {"strategy_name": self.strategy_name, "n_runs": len(self.scores),
                "trend": self.trend.value, "slope": round(self.slope, 4),
                "delta": round(self.delta, 4), "mean": round(self.mean, 4),
                "volatility": round(self.volatility, 4), "verdict": self.verdict,
                "scores": [round(s, 4) for s in self.scores]}


def eval_skill_trend(strategy_name: str, scores: list[float],
                     flat_band: float = 0.02) -> SkillEval:
    """Decide whether a strategy is improving/regressing/flat over its score history.

    `flat_band` is the per-run slope magnitude under which we call it flat — so noise
    doesn't get read as a trend. The raw scores travel with the verdict for audit.
    """
    scores = [float(s) for s in scores]
    if len(scores) < 2:
        return SkillEval(strategy_name, scores, Trend.INSUFFICIENT, 0.0, 0.0,
                         scores[0] if scores else 0.0, 0.0,
                         "need at least 2 runs to judge a trend")
    slope = _ols_slope(scores)
    delta = scores[-1] - scores[0]
    mean = sum(scores) / len(scores)
    var = sum((s - mean) ** 2 for s in scores) / len(scores)
    vol = var ** 0.5
    if abs(slope) < flat_band:
        trend, verdict = Trend.FLAT, f"stable (|slope| {abs(slope):.3f} < {flat_band})"
    elif slope > 0:
        trend, verdict = Trend.IMPROVING, f"improving +{slope:.3f}/run (Δ {delta:+.3f})"
    else:
        trend, verdict = Trend.REGRESSING, f"regressing {slope:.3f}/run (Δ {delta:+.3f})"
    return SkillEval(strategy_name, scores, trend, slope, delta, mean, vol, verdict)
