import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from notelab.research.evals import RunnerSpec
from notelab.research.reliability import filter_known_unreliable


def _make_mml_ledger(path: Path, notes: list[tuple[str, str, str]]) -> None:
    """notes: list of (agent, note_text, written_at ISO string)."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE agent_notes (id INTEGER PRIMARY KEY, agent TEXT, note_text TEXT, "
        "written_at TEXT, task_context TEXT, tags TEXT, written_by TEXT, confidence REAL)"
    )
    for agent, note_text, written_at in notes:
        conn.execute(
            "INSERT INTO agent_notes (agent, note_text, written_at) VALUES (?, ?, ?)",
            (agent, note_text, written_at),
        )
    conn.commit()
    conn.close()


def _make_mml_ledger_epoch_schema(path: Path, notes: list[tuple[str, str, float]]) -> None:
    """Matches the REAL [redacted-private-project] production schema (verified live against
    ~/.local/state/[redacted-private-project]/swarm-runs.db): `timestamp` is a REAL unix-epoch float,
    there is no `written_at` column at all. notes: list of (agent, note_text,
    timestamp epoch float)."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE agent_notes (id INTEGER PRIMARY KEY, agent TEXT, timestamp REAL, "
        "task_context TEXT, note_text TEXT, tags TEXT, written_by TEXT, confidence REAL)"
    )
    for agent, note_text, ts in notes:
        conn.execute(
            "INSERT INTO agent_notes (agent, note_text, timestamp) VALUES (?, ?, ?)",
            (agent, note_text, ts),
        )
    conn.commit()
    conn.close()


def test_provider_with_recent_quota_note_is_marked_unavailable(tmp_path):
    ledger = tmp_path / "mml-ledger.db"
    _make_mml_ledger(ledger, [
        ("codex", "hit monthly usage limit, try again Oct 29", "2026-09-30T10:00:00+00:00"),
    ])
    specs = [RunnerSpec("codex", "codex", ["exec", "{prompt}"], available=True,
                        path="/usr/bin/codex", provider="codex")]
    out = filter_known_unreliable(specs, mml_ledger_path=ledger, now_iso="2026-09-30T12:00:00+00:00")
    assert out[0].available is False
    assert "quota" in (out[0].skipped_reason or "").lower()


def test_provider_with_no_notes_is_left_untouched(tmp_path):
    ledger = tmp_path / "mml-ledger.db"
    _make_mml_ledger(ledger, [])
    specs = [RunnerSpec("muse", "muse", ["exec", "{prompt}"], available=True,
                        path="/usr/bin/muse", provider="muse")]
    out = filter_known_unreliable(specs, mml_ledger_path=ledger, now_iso="2026-09-30T12:00:00+00:00")
    assert out[0].available is True


def test_missing_ledger_file_is_a_noop_not_an_error(tmp_path):
    specs = [RunnerSpec("codex", "codex", ["exec", "{prompt}"], available=True,
                        path="/usr/bin/codex", provider="codex")]
    out = filter_known_unreliable(specs, mml_ledger_path=tmp_path / "does-not-exist.db",
                                  now_iso="2026-09-30T12:00:00+00:00")
    assert out[0].available is True


def test_old_quota_note_past_the_window_is_ignored(tmp_path):
    ledger = tmp_path / "mml-ledger.db"
    _make_mml_ledger(ledger, [
        ("codex", "hit monthly usage limit", "2026-08-01T10:00:00+00:00"),
    ])
    specs = [RunnerSpec("codex", "codex", ["exec", "{prompt}"], available=True,
                        path="/usr/bin/codex", provider="codex")]
    out = filter_known_unreliable(specs, mml_ledger_path=ledger, now_iso="2026-09-30T12:00:00+00:00")
    assert out[0].available is True


def test_real_epoch_schema_ledger_is_read_correctly(tmp_path):
    """The REAL [redacted-private-project] ledger uses `timestamp` (REAL unix-epoch), not `written_at`
    (TEXT/ISO) -- this exercises the _time_column()/_parse_epoch() branch that matches
    production, which the other tests (all built on the ISO `written_at` fixture)
    never touch."""
    ledger = tmp_path / "mml-ledger-epoch.db"
    # 2026-09-30T10:00:00+00:00 as a unix-epoch float, same wall-clock moment as the
    # ISO fixture used in test_provider_with_recent_quota_note_is_marked_unavailable.
    written_epoch = datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc).timestamp()
    _make_mml_ledger_epoch_schema(ledger, [
        ("kiro-cli", "race task 'x': quota_exhausted, 9070ms", written_epoch),
    ])
    specs = [RunnerSpec("kiro-cli", "kiro-cli", ["chat", "{prompt}", "--no-interactive"],
                        available=True, path="/usr/bin/kiro-cli", provider="kiro-cli")]
    out = filter_known_unreliable(specs, mml_ledger_path=ledger,
                                  now_iso="2026-09-30T12:00:00+00:00")
    assert out[0].available is False
    assert "quota" in (out[0].skipped_reason or "").lower()


def test_old_epoch_quota_note_past_the_window_is_ignored(tmp_path):
    """Same as test_old_quota_note_past_the_window_is_ignored but through the epoch
    branch -- confirms the epoch-parsed datetime is comparable to now_iso's parsed
    datetime for the window check, not just for the flag/no-flag outcome."""
    ledger = tmp_path / "mml-ledger-epoch.db"
    written_epoch = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp()
    _make_mml_ledger_epoch_schema(ledger, [
        ("kiro-cli", "quota_exhausted", written_epoch),
    ])
    specs = [RunnerSpec("kiro-cli", "kiro-cli", ["chat", "{prompt}", "--no-interactive"],
                        available=True, path="/usr/bin/kiro-cli", provider="kiro-cli")]
    out = filter_known_unreliable(specs, mml_ledger_path=ledger,
                                  now_iso="2026-09-30T12:00:00+00:00")
    assert out[0].available is True


def test_most_recent_note_per_agent_wins(tmp_path):
    """An agent with an OLDER quota note and a NEWER clean note should be left
    available -- only the most recent note per agent decides current status, a
    resolved quota issue must not keep flagging an agent forever."""
    ledger = tmp_path / "mml-ledger.db"
    _make_mml_ledger(ledger, [
        ("codex", "hit monthly usage limit", "2026-09-30T09:00:00+00:00"),
        ("codex", "no_diff, ran fine", "2026-09-30T11:00:00+00:00"),
    ])
    specs = [RunnerSpec("codex", "codex", ["exec", "{prompt}"], available=True,
                        path="/usr/bin/codex", provider="codex")]
    out = filter_known_unreliable(specs, mml_ledger_path=ledger, now_iso="2026-09-30T12:00:00+00:00")
    assert out[0].available is True


def test_table_with_neither_time_column_is_a_noop_not_an_error(tmp_path):
    """A ledger whose agent_notes has neither `written_at` nor `timestamp` (some
    future/foreign schema) must be treated like a missing/unreadable ledger: never
    raise, leave availability exactly as detect_runners() already set it."""
    ledger = tmp_path / "mml-ledger-weird-schema.db"
    conn = sqlite3.connect(str(ledger))
    conn.execute("CREATE TABLE agent_notes (id INTEGER PRIMARY KEY, agent TEXT, note_text TEXT)")
    conn.execute("INSERT INTO agent_notes (agent, note_text) VALUES (?, ?)",
                ("codex", "hit monthly usage limit"))
    conn.commit()
    conn.close()
    specs = [RunnerSpec("codex", "codex", ["exec", "{prompt}"], available=True,
                        path="/usr/bin/codex", provider="codex")]
    out = filter_known_unreliable(specs, mml_ledger_path=ledger, now_iso="2026-09-30T12:00:00+00:00")
    assert out[0].available is True


def test_family_prefix_variant_with_recent_quota_note_is_marked_unavailable(tmp_path):
    """The real [redacted-private-project] ledger records variants like 'kilo-auto', never a bare
    'kilo' -- a note for a variant must still flag the family's RunnerSpec
    (name='kilo'), via family-prefix matching ('kilo-' prefix), not just exact
    name/provider equality."""
    ledger = tmp_path / "mml-ledger.db"
    _make_mml_ledger(ledger, [
        ("kilo-auto", "hit monthly usage limit", "2026-09-30T10:00:00+00:00"),
    ])
    specs = [RunnerSpec("kilo", "kilo", ["run", "-m", "kilo/kilo-auto/free", "{prompt}"],
                        available=True, path="/usr/bin/kilo", provider="kilo")]
    out = filter_known_unreliable(specs, mml_ledger_path=ledger, now_iso="2026-09-30T12:00:00+00:00")
    assert out[0].available is False
    assert "quota" in (out[0].skipped_reason or "").lower()


def test_family_prefix_variant_with_stale_quota_note_is_left_untouched(tmp_path):
    """A stale (>14 day) quota note for a variant must NOT flip the family spec --
    exercises the window check still applies under family-prefix matching."""
    ledger = tmp_path / "mml-ledger.db"
    _make_mml_ledger(ledger, [
        ("kilo-auto", "hit monthly usage limit", "2026-08-01T10:00:00+00:00"),
    ])
    specs = [RunnerSpec("kilo", "kilo", ["run", "-m", "kilo/kilo-auto/free", "{prompt}"],
                        available=True, path="/usr/bin/kilo", provider="kilo")]
    out = filter_known_unreliable(specs, mml_ledger_path=ledger, now_iso="2026-09-30T12:00:00+00:00")
    assert out[0].available is True


def test_filter_known_unreliable_closes_connection_on_time_column_error(tmp_path, monkeypatch):
    """If _time_column(conn) raises (e.g. a corrupt/foreign schema causing some other
    sqlite3.Error upstream of the known neither-column case), the connection it opened
    must still be closed -- no leaked sqlite3 connection on the error path."""
    import sqlite3 as sqlite3_module

    from notelab.research import reliability

    ledger = tmp_path / "mml-ledger.db"
    conn = sqlite3_module.connect(str(ledger))
    conn.execute("CREATE TABLE agent_notes (id INTEGER PRIMARY KEY, agent TEXT, note_text TEXT)")
    conn.commit()
    conn.close()

    opened_connections = []
    real_connect = sqlite3_module.connect

    def tracking_connect(*args, **kwargs):
        c = real_connect(*args, **kwargs)
        opened_connections.append(c)
        return c

    monkeypatch.setattr(reliability.sqlite3, "connect", tracking_connect)

    def boom(_conn):
        raise sqlite3_module.OperationalError("simulated failure reading schema")

    monkeypatch.setattr(reliability, "_time_column", boom)

    specs = [RunnerSpec("codex", "codex", ["exec", "{prompt}"], available=True,
                        path="/usr/bin/codex", provider="codex")]
    filter_known_unreliable(specs, mml_ledger_path=ledger, now_iso="2026-09-30T12:00:00+00:00")

    assert len(opened_connections) == 1
    # sqlite3.Connection has no public "closed" attribute; a closed connection
    # raises ProgrammingError on any further use.
    try:
        opened_connections[0].execute("SELECT 1")
        closed = False
    except sqlite3_module.ProgrammingError:
        closed = True
    assert closed, "connection opened by filter_known_unreliable was never closed"
