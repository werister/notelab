"""
Reliability filter: skip adversarial runners that an *external* run-ledger has
already recorded as currently unreliable (quota-exhausted, consistently
garbled/empty output), instead of notelab re-discovering the same failures live
on every sparring run.

The ledger is any plain SQLite file with an `agent_notes` table — no server, no
shared package, the loosest possible coupling that still shares real signal. Point
notelab at one with the `NOTELAB_RELIABILITY_LEDGER` environment variable (or pass
`ledger_path=`). If the file does not exist (the common case — most installs have
no such ledger), this is a silent no-op: notelab always works standalone.

Schema: the filter reads `agent` (runner name/variant), `note_text` (free text), and
a time column. It accepts either `written_at` (TEXT, ISO 8601) or `timestamp` (REAL
unix-epoch) and detects whichever the table actually has, so it works against an
ISO-based ledger and an epoch-based one alike.
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .evals import RunnerSpec

# Default ledger location, overridable via env. Empty/unset -> no external ledger,
# the filter is a pure no-op and notelab relies on live PATH detection alone.
DEFAULT_RELIABILITY_LEDGER = Path(
    os.environ.get("NOTELAB_RELIABILITY_LEDGER", "")
) if os.environ.get("NOTELAB_RELIABILITY_LEDGER") else None

# how recent a quota-exhaustion note has to be to still count -- monthly quotas reset,
# so a note from last month says nothing about right now.
_QUOTA_NOTE_WINDOW = timedelta(days=14)

# Phrases in a note that mark a provider as currently quota/usage limited, e.g.
# "race task 'auto-pick-live-demo': quota_exhausted, 8255ms" -- "quota" alone covers
# that; "usage limit" / "monthly limit" cover the plainer prose form.
_QUOTA_HINTS = re.compile(r"quota|usage limit|monthly limit", re.I)


def _parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_epoch(value) -> datetime:
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _time_column(conn: sqlite3.Connection) -> tuple[str, str]:
    """Return (column_name, kind) where kind is 'iso' or 'epoch'. Raises sqlite3.Error
    if agent_notes has neither -- callers already catch sqlite3.Error as a no-op."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_notes)").fetchall()}
    if "written_at" in cols:
        return "written_at", "iso"
    if "timestamp" in cols:
        return "timestamp", "epoch"
    raise sqlite3.Error("agent_notes has neither 'written_at' nor 'timestamp' column")


def filter_known_unreliable(
    specs: list[RunnerSpec],
    ledger_path: Path | str | None = None,
    now_iso: str | None = None,
) -> list[RunnerSpec]:
    """Return specs with .available/.skipped_reason overridden to False for any
    provider whose most recent `agent_notes` entry, within the last
    `_QUOTA_NOTE_WINDOW`, mentions a quota/usage-limit failure.

    Never raises: a missing/unreadable ledger, no configured ledger, or a provider
    with no notes at all leaves that spec's availability exactly as detect_runners()
    already set it.
    """
    resolved = ledger_path if ledger_path is not None else DEFAULT_RELIABILITY_LEDGER
    if resolved is None:
        return specs
    path = Path(resolved)
    if not path.exists():
        return specs
    now = _parse_iso(now_iso) if now_iso else datetime.now(timezone.utc)

    quota_flagged: set[str] = set()
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            time_col, time_kind = _time_column(conn)
            rows = conn.execute(
                f"SELECT agent, note_text, {time_col} AS ts FROM agent_notes "
                f"ORDER BY {time_col} DESC"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return specs

    seen: set[str] = set()
    for row in rows:
        agent = row["agent"]
        if agent in seen:
            continue  # only the MOST RECENT note per agent decides current status
        seen.add(agent)
        try:
            written = _parse_iso(row["ts"]) if time_kind == "iso" else _parse_epoch(row["ts"])
        except (ValueError, TypeError):
            continue
        if now - written > _QUOTA_NOTE_WINDOW:
            continue
        if _QUOTA_HINTS.search(row["note_text"] or ""):
            quota_flagged.add(agent)

    out = []
    for spec in specs:
        if _spec_matches_any(spec, quota_flagged):
            out.append(_mark_unavailable(spec))
        else:
            out.append(spec)
    return out


def _spec_matches_any(spec: RunnerSpec, agents: set[str]) -> bool:
    """True if any flagged ledger agent identifies this spec: an exact match on
    spec.name/spec.provider, or a family-prefix match ('<name>-'/'<provider>-').
    Ledgers often record model variants (e.g. 'kilo-auto', 'cline-glm-5-3') rather
    than the bare family name, so prefix matching is required without hardcoding
    specific variant ids."""
    keys = {k for k in (spec.name, spec.provider) if k}
    for agent in agents:
        if agent in keys:
            return True
        if any(agent.startswith(f"{key}-") for key in keys):
            return True
    return False


def _mark_unavailable(spec: RunnerSpec) -> RunnerSpec:
    return RunnerSpec(spec.name, spec.bin, list(spec.args_template),
                      available=False, path=spec.path, provider=spec.provider,
                      skipped_reason="recent quota exhaustion reported in the reliability ledger")
