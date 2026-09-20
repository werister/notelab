"""Catalog sync + search tests — offline, fake lister, temp SQLite."""
from notelab.catalog import search_catalog, sync_catalog
from notelab.research.quality import SourceFeatures
from notelab.storage import Store


class FakeLister:
    def __init__(self, notebooks, sources_by_nb, fail_on=None):
        self._nbs = notebooks
        self._src = sources_by_nb
        self._fail_on = fail_on or set()

    def list_notebooks(self):
        return self._nbs

    def list_sources(self, notebook_id):
        if notebook_id in self._fail_on:
            raise RuntimeError("boom")
        return self._src.get(notebook_id, [])


def _src(id, title, url):
    return SourceFeatures(id=id, title=title, url=url)


def test_sync_indexes_notebooks_and_sources(tmp_path):
    store = Store(tmp_path / "c.db")
    lister = FakeLister(
        notebooks=[{"id": "nb1", "title": "Quantum"}, {"id": "nb2", "title": "Biology"}],
        sources_by_nb={
            "nb1": [_src("s1", "Quantum error correction", "https://arxiv.org/1"),
                    _src("s2", "Surface codes", "https://nature.com/2")],
            "nb2": [_src("s3", "CRISPR overview", "https://nih.gov/3")],
        })
    report = sync_catalog(store, lister)
    assert report.notebooks == 2
    assert report.sources == 3
    assert report.errors == []
    nbs = store.list_notebooks()
    assert {n["id"] for n in nbs} == {"nb1", "nb2"}
    assert {n["n_sources"] for n in nbs} == {1, 2}
    store.close()


def test_search_finds_indexed_source(tmp_path):
    store = Store(tmp_path / "c.db")
    lister = FakeLister(
        notebooks=[{"id": "nb1", "title": "Quantum"}],
        sources_by_nb={"nb1": [_src("s1", "Quantum error correction codes", "https://arxiv.org/1")]})
    sync_catalog(store, lister)
    hits = search_catalog(store, "quantum")
    assert hits and hits[0]["id"] == "s1"
    assert hits[0]["notebook_id"] == "nb1"
    assert search_catalog(store, "sourdough") == []
    store.close()


def test_sync_survives_a_failing_notebook(tmp_path):
    store = Store(tmp_path / "c.db")
    lister = FakeLister(
        notebooks=[{"id": "ok", "title": "OK"}, {"id": "bad", "title": "Bad"}],
        sources_by_nb={"ok": [_src("s1", "good", "https://arxiv.org/1")]},
        fail_on={"bad"})
    report = sync_catalog(store, lister)
    assert report.notebooks == 2       # both notebooks upserted
    assert report.sources == 1         # only ok's source indexed
    assert any("bad" in e for e in report.errors)
    store.close()


def test_sync_restricted_to_given_ids(tmp_path):
    store = Store(tmp_path / "c.db")
    lister = FakeLister(
        notebooks=[{"id": "nb1", "title": "A"}, {"id": "nb2", "title": "B"}],
        sources_by_nb={"nb1": [_src("s1", "x", "https://a.com/1")],
                       "nb2": [_src("s2", "y", "https://b.com/2")]})
    report = sync_catalog(store, lister, notebook_ids=["nb1"])
    assert report.notebooks == 1
    assert report.sources == 1
    store.close()


def test_resync_updates_not_duplicates(tmp_path):
    store = Store(tmp_path / "c.db")
    lister = FakeLister(notebooks=[{"id": "nb1", "title": "A"}],
                        sources_by_nb={"nb1": [_src("s1", "old title", "https://a.com/1")]})
    sync_catalog(store, lister)
    lister._src["nb1"] = [_src("s1", "new title", "https://a.com/1")]
    sync_catalog(store, lister)
    hits = search_catalog(store, "new")
    assert len(hits) == 1 and hits[0]["id"] == "s1"
    assert search_catalog(store, "old") == []  # FTS updated, not duplicated
    store.close()
