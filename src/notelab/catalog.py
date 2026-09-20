"""
Catalog — the indexed filesystem for agents (M2, the "Obsidian alternative").

NotebookLM holds the sources; this holds a LOCAL, queryable INDEX of what lives in
which notebook, so an agent can ask "what do we already know about X" **before**
spending a Deep Research run. It's the retrieval-before-research layer.

`sync_catalog` walks the notebooks (via the nlmx adapter) into the SQLite catalog +
FTS index; `search_catalog` answers a full-text query over source titles/urls. This is
decoupled from the adapter — it takes a `Lister` protocol — so it tests with a fake and
never needs the network.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .research.quality import SourceFeatures
from .storage import Store


class Lister(Protocol):
    """What the catalog needs from a source of notebooks/sources (the nlmx adapter)."""
    def list_notebooks(self) -> list[dict]: ...
    def list_sources(self, notebook_id: str) -> list[SourceFeatures]: ...


@dataclass
class SyncReport:
    notebooks: int
    sources: int
    errors: list[str]

    def as_dict(self) -> dict:
        return {"notebooks_synced": self.notebooks, "sources_synced": self.sources,
                "errors": self.errors}


def sync_catalog(store: Store, lister: Lister,
                 notebook_ids: list[str] | None = None) -> SyncReport:
    """Index notebooks + their sources into the local catalog.

    notebook_ids: restrict to these; None syncs every notebook the account can see.
    Per-notebook failures are collected, not raised, so one bad notebook doesn't
    abort a full sync.
    """
    errors: list[str] = []
    n_nb = n_src = 0

    if notebook_ids is not None:
        notebooks = [{"id": nid, "title": ""} for nid in notebook_ids]
    else:
        try:
            notebooks = lister.list_notebooks()
        except Exception as e:
            return SyncReport(0, 0, [f"list_notebooks: {type(e).__name__}: {e}"])

    for nb in notebooks:
        nb_id = nb.get("id")
        if not nb_id:
            continue
        store.upsert_notebook(nb_id, nb.get("title", ""))
        n_nb += 1
        try:
            sources = lister.list_sources(nb_id)
        except Exception as e:
            errors.append(f"{nb_id}: {type(e).__name__}: {e}")
            continue
        for s in sources:
            store.upsert_source(s.id, nb_id, s.title, s.url, s.domain or "")
            n_src += 1

    return SyncReport(n_nb, n_src, errors)


def search_catalog(store: Store, query: str, limit: int = 20) -> list[dict]:
    """Full-text search the indexed sources. Returns rows with notebook + domain, so
    an agent knows WHICH notebook already holds a source before researching again."""
    return store.search_sources(query, limit=limit)
