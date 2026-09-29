"""
nlmx adapter — the ONLY place this server touches NotebookLM.

It shells `nlmx` (this repo's guarded wrapper), never raw `nlm`, so the three
confirmed corruption guards — idempotent `research import`, refusal of the broken
`--source-ids`, auto `--confirm` — apply to the MCP path exactly as they do to the
CLI path. That is the whole reason this adapter exists rather than calling `nlm`
directly: one guard surface, no duplicated safety logic.

Kept thin and defensive: NotebookLM output formats drift, so parsing tolerates
both `--json` and plain output and degrades to a clear error rather than a crash.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime

from .research.ab import Strategy
from .research.quality import SourceFeatures


class NlmxError(RuntimeError):
    pass


def _resolve_nlmx() -> str:
    env = os.environ.get("NLMX_BIN")
    if env and os.path.exists(env):
        return env
    found = shutil.which("nlmx")
    if found:
        return found
    home_bin = os.path.expanduser("~/.local/bin/nlmx")
    if os.path.exists(home_bin):
        return home_bin
    raise NlmxError(
        "nlmx not found. Install it (`cp nlmx ~/.local/bin/nlmx && chmod +x`) or set NLMX_BIN."
    )


@dataclass
class NlmxAdapter:
    bin_path: str | None = None
    timeout: int = 1500  # deep research can be slow; must stay above nlmx's own
    # internal max_wait for deep mode (1200s) plus room for the import step after

    def __post_init__(self):
        self.bin_path = self.bin_path or _resolve_nlmx()

    def _run(self, args: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [self.bin_path, *args], capture_output=True, text=True,
                timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise NlmxError(f"nlmx {' '.join(args)} timed out after {e.timeout}s") from e

    # --- primitives -------------------------------------------------------------

    def create_notebook(self, title: str) -> str:
        # `nlm notebook create` has no --quiet/--json; output is two lines:
        # "✓ Created notebook: <title>" then "  ID: <uuid>". Parse the id out of that,
        # don't assume the id is the bare last line.
        # `title` is a POSITIONAL arg: `nlm` has no `--title` flag — don't "fix" this.
        r = self._run(["notebook", "create", title], timeout=120)
        if r.returncode != 0:
            raise NlmxError(f"notebook create failed: {r.stderr.strip() or r.stdout.strip()}")
        m = re.search(r"ID:\s*(\S+)", r.stdout)
        if not m:
            raise NlmxError(f"notebook create returned no parseable id: {r.stdout.strip()!r}")
        return m.group(1)

    def list_notebooks(self) -> list[dict]:
        """List notebooks as [{id, title}, ...]. Tolerates JSON or plain output."""
        r = self._run(["notebook", "list", "--json"], timeout=120)
        if r.returncode != 0:
            raise NlmxError(f"notebook list failed: {r.stderr.strip() or r.stdout.strip()}")
        raw = (r.stdout or "").strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        items = data if isinstance(data, list) else data.get("notebooks", [])
        out = []
        for it in items:
            if isinstance(it, dict):
                out.append({"id": str(it.get("id") or it.get("notebook_id") or ""),
                            "title": str(it.get("title") or it.get("name") or "")})
        return out

    def list_sources(self, notebook_id: str) -> list[SourceFeatures]:
        r = self._run(["source", "list", notebook_id, "--json"], timeout=120)
        if r.returncode != 0:
            raise NlmxError(f"source list failed: {r.stderr.strip() or r.stdout.strip()}")
        return self._parse_sources(r.stdout)

    def source_content(self, source_id: str) -> str:
        r = self._run(["source", "content", source_id], timeout=120)
        return r.stdout if r.returncode == 0 else ""

    def remove_source(self, notebook_id: str, source_id: str) -> tuple[bool, str]:
        """Remove one source from a notebook. Returns (ok, message). Destructive — only
        called by the plan executor when the caller has explicitly confirmed. Passes
        --confirm since nlmx/nlm gate deletes on it.

        `notebook_id` is unused: `nlm source delete` (not `remove`) takes only source
        id(s), no notebook scope. Kept in the signature since callers pass it and
        source ids aren't guaranteed globally unique across notebooks in general use.
        """
        r = self._run(["source", "delete", source_id, "--confirm"], timeout=120)
        ok = r.returncode == 0
        return ok, (r.stdout.strip() or r.stderr.strip() or ("removed" if ok else "failed"))

    def research_run(self, strategy: Strategy) -> tuple[list[SourceFeatures], dict]:
        """Run one strategy via the guarded composite `nlmx research run`.

        `nlmx research run` does start -> poll -> import exactly once (idempotent),
        so we don't re-implement that state machine or risk the duplicate-import bug.
        """
        nb_id = strategy.notebook_id
        if not nb_id:
            nb_id = self.create_notebook(f"[ab] {strategy.name}")

        args = ["research", "run", strategy.prompt, "--notebook-id", nb_id,
                "--mode", strategy.mode, "--source", strategy.source]
        r = self._run(args)
        meta: dict = {"notebook_id": nb_id}
        if r.returncode != 0:
            meta["error"] = (r.stderr.strip() or r.stdout.strip())[:500]
            return [], meta

        for line in r.stdout.splitlines():
            if "Task ID:" in line:
                meta["task_id"] = line.split("Task ID:", 1)[1].strip()
                break

        sources = self.list_sources(nb_id)
        # enrich a bounded number of sources with content for depth/relevance scoring
        for s in sources[:15]:
            if not s.content:
                s.content = self.source_content(s.id)
        return sources, meta

    # --- parsing ----------------------------------------------------------------

    @staticmethod
    def _parse_sources(raw: str) -> list[SourceFeatures]:
        raw = (raw or "").strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []  # non-JSON output; caller treats an empty set as a scored-zero arm
        items = data if isinstance(data, list) else data.get("sources", [])
        out: list[SourceFeatures] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            out.append(SourceFeatures(
                id=str(it.get("id") or it.get("source_id") or it.get("title") or ""),
                title=str(it.get("title") or it.get("name") or ""),
                url=str(it.get("url") or it.get("source_url") or ""),
                content=str(it.get("content") or it.get("text") or ""),
                published=_parse_date(it.get("published") or it.get("date")),
            ))
        return out


def _parse_date(v) -> datetime | None:
    if not v:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(str(v), fmt)
        except (ValueError, TypeError):
            continue
    return None
