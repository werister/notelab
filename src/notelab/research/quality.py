"""
Data-quality scoring for a NotebookLM research run's SOURCE set.

This scores *what got retrieved* (the sources), not the prose of a synthesized
answer. It is deliberately deterministic and LLM-free so scores are reproducible,
auditable, and free to compute — the A/B harness ranks prompt strategies on this
signal without spending a single token.

Every source is reduced to a `SourceFeatures`, each signal is a 0..1 score, and
the composite is a weighted sum where the weights are *config*, not code. Nothing
here talks to the network; callers pass in already-fetched source metadata/content
(see the nlmx adapter), which keeps this module unit-testable against fixtures.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from urllib.parse import urlparse

# --- Domain authority heuristics -------------------------------------------------
# Not an exhaustive ranking — a transparent starting allow/deny signal a user can
# extend via QualityWeights.authority_overrides. TLD-level rules catch the long tail.

_HIGH_AUTHORITY_TLDS = {"gov", "edu", "int", "mil"}
_HIGH_AUTHORITY_DOMAINS = {
    "arxiv.org", "nature.com", "science.org", "acm.org", "ieee.org",
    "nih.gov", "who.int", "nist.gov", "pnas.org", "cell.com",
    "sciencedirect.com", "springer.com", "jstor.org", "ssrn.com",
    "docs.python.org", "developer.mozilla.org", "kernel.org", "rfc-editor.org",
    "wikipedia.org", "reuters.com", "apnews.com", "bls.gov", "oecd.org",
}
_LOW_AUTHORITY_DOMAINS = {
    "medium.com", "quora.com", "pinterest.com", "answers.com",
    "ehow.com", "wikihow.com", "buzzfeed.com",
}
# Content-farm / SEO-spam shaped hostnames get a small penalty.
_SPAM_HINTS = re.compile(r"(top\d+|best-?\d+|listicle|clickbait|cheap|-deals?)", re.I)

# --- Source-type classification --------------------------------------------------
# NoteLab's premise is a *diverse* research library — papers, docs, repos, step-by-step
# videos, and community discussion each answer a different question about a topic. The
# scorer therefore recognizes a source's KIND (not just its domain) so set-level
# diversity can reward a balanced mix, and so a video/discussion isn't mistaken for a
# low-authority web page. Classification is deterministic and URL/title-driven — no
# network, no model. Unknown sources fall back to "web" (neutral).

_VIDEO_DOMAINS = {"youtube.com", "youtu.be", "vimeo.com"}
_DISCUSSION_DOMAINS = {
    "reddit.com", "ycombinator.com", "stackoverflow.com", "stackexchange.com",
    "quora.com", "discourse.org", "substack.com",
}
_REPO_DOMAINS = {"github.com", "gitlab.com", "bitbucket.org", "sourceforge.net"}
_PAPER_DOMAINS = {
    "arxiv.org", "ssrn.com", "jstor.org", "pnas.org", "nature.com", "science.org",
    "acm.org", "ieee.org", "springer.com", "sciencedirect.com", "cell.com",
    "biorxiv.org", "medrxiv.org", "openreview.net", "semanticscholar.org", "doi.org",
    "aclanthology.org", "preprints.org", "ceur-ws.org", "alphaxiv.org", "mlr.press",
    "neurips.cc", "proceedings.mlr.press",
}
_DOCS_DOMAINS = {
    "docs.python.org", "developer.mozilla.org", "readthedocs.io", "readthedocs.org",
    "rfc-editor.org", "kernel.org", "wikipedia.org",
}
_REFERENCE_DOMAINS = {"wikipedia.org", "wikibooks.org", "wiktionary.org"}

# ordered: first match wins; the five "diverse-library" kinds a research notebook
# ideally spans. "reference" and "web" are the neutral fallbacks.
SOURCE_TYPES = ("paper", "docs", "repo", "video", "discussion", "reference", "web")

# Markers of a "bot-wall" / sign-in shell — e.g. a YouTube link that NotebookLM's
# Deep Research imported as an ordinary web page and got a "Sign in to confirm you're
# not a bot" screen instead of the transcript. Verified against real live imports: the
# shell is dominated by navigation chrome and contains no substantive content. Such a
# source should not earn full depth or count as genuine kind-diversity.
_BLOCKED_MARKERS = (
    "sign in to confirm", "skip navigation", "search with your voice",
    "confirm you're not a bot", "accounts.google.com/servicelogin",
    "enable javascript", "please enable cookies",
)


def is_blocked_content(content: str) -> bool:
    """True if `content` looks like a sign-in / bot-wall shell rather than real
    substance. Heuristic and deterministic: fires when several nav-chrome markers are
    present (a single incidental mention isn't enough)."""
    if not content:
        return False
    low = content.lower()
    hits = sum(1 for m in _BLOCKED_MARKERS if m in low)
    return hits >= 2


def classify_source_type(url: str, title: str = "") -> str:
    """Classify a source into one of SOURCE_TYPES from its URL (and title as a hint).
    Deterministic and offline. Unknown -> 'web'."""
    dom = _domain(url)
    reg = _registrable(dom)
    t = (title or "").lower()
    if not dom:
        # non-web source (uploaded PDF, pasted text): a title ending in .pdf or
        # mentioning "paper"/"preprint" leans paper, else neutral web.
        if t.endswith(".pdf") or "preprint" in t or "arxiv" in t:
            return "paper"
        return "web"
    if reg in _VIDEO_DOMAINS or "/watch" in url or "youtu.be/" in url:
        return "video"
    if reg in _REPO_DOMAINS:
        return "repo"
    if reg in _PAPER_DOMAINS:
        return "paper"
    if reg in _DISCUSSION_DOMAINS:
        return "discussion"
    if reg in _REFERENCE_DOMAINS:
        return "reference"
    if reg in _DOCS_DOMAINS or dom.startswith("docs.") or "/docs/" in url or t.endswith(".pdf"):
        return "docs"
    return "web"


_WORD_RE = re.compile(r"[a-z0-9]+", re.I)
_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "is", "are",
    "was", "were", "be", "with", "by", "as", "at", "that", "this", "it", "from",
    "how", "what", "why", "when", "which", "who", "does", "do", "can", "vs",
}


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except (ValueError, AttributeError):
        return ""
    return host[4:] if host.startswith("www.") else host


def _registrable(domain: str) -> str:
    """Crude eTLD+1 (good enough for diversity/authority; not a public-suffix parser)."""
    parts = domain.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text or "")]


def _content_terms(text: str) -> set[str]:
    return {w for w in _tokens(text) if w not in _STOPWORDS and len(w) > 2}


@dataclass
class SourceFeatures:
    """Normalized view of one NotebookLM source. All fields optional but url/title help."""
    id: str
    title: str = ""
    url: str = ""
    content: str = ""
    published: datetime | None = None

    # optional explicit override; when "" the type is derived from url/title.
    source_type: str = ""

    @property
    def domain(self) -> str:
        return _domain(self.url)

    @property
    def registrable(self) -> str:
        return _registrable(self.domain)

    @property
    def kind(self) -> str:
        """Source type (paper/docs/repo/video/discussion/reference/web). Uses an
        explicit `source_type` if set, else classifies from url/title."""
        return self.source_type or classify_source_type(self.url, self.title)

    @property
    def is_blocked(self) -> bool:
        """True if this source's content looks like a sign-in / bot-wall shell
        (e.g. a YouTube link imported as a page) rather than real substance."""
        return is_blocked_content(self.content)


@dataclass
class QualityWeights:
    """Signal weights — this is the tuning surface. Weights need not sum to 1."""
    authority: float = 0.25
    diversity: float = 0.20
    recency: float = 0.15
    depth: float = 0.20
    relevance: float = 0.20
    # tuning knobs
    recency_halflife_days: float = 365.0  # smaller = more aggressively favor fresh
    depth_target_chars: int = 4000        # content length that scores ~1.0 on depth
    # how much the diversity signal counts research-KIND spread (paper/video/repo/...)
    # vs raw domain spread. 0.5 = equal; 0.0 = legacy domain-only behaviour.
    diversity_type_weight: float = 0.5
    authority_overrides: dict[str, float] = field(default_factory=dict)  # domain -> 0..1
    # question term -> set of synonym/related terms that ALSO count as covering it.
    # Deterministic, config-driven semantic-lite matching (no embeddings/network):
    # e.g. {"decoder": {"decoding", "bp", "mwpm"}}. A source covers the question term
    # if it contains the term OR any of its expansions.
    term_expansions: dict[str, set[str]] = field(default_factory=dict)
    # Per-KIND value weights — the "user owns what they value" knob. Maps a source
    # kind (paper/docs/repo/video/discussion/reference/web) to a 0..N multiplier on
    # that source's contribution to the composite. Empty = every kind counts equally
    # (today's neutral default). A weight of 0.0 means "ignore this kind entirely"
    # (it won't raise the score and won't earn kind-diversity credit). This is how a
    # user says "for a lit review I value papers, not forum posts" or "for learning a
    # tool I value step-by-step videos and discussion above papers" — see
    # docs/DESIGN-PRINCIPLE-user-owns-value.md. There is NO fixed global ranking of
    # kinds; this is purely the caller's choice.
    kind_weights: dict[str, float] = field(default_factory=dict)

    def normalized(self) -> "QualityWeights":
        """Return a copy with the five signal weights scaled to sum to 1. Uses
        dataclasses.replace so every config field (incl. future ones) is carried
        automatically — never hand-list fields here again."""
        total = self.authority + self.diversity + self.recency + self.depth + self.relevance
        if total <= 0:
            return self
        return replace(
            self,
            authority=self.authority / total,
            diversity=self.diversity / total,
            recency=self.recency / total,
            depth=self.depth / total,
            relevance=self.relevance / total,
        )

    def kind_weight(self, kind: str) -> float:
        """The value multiplier for a source kind. Default 1.0 when no per-kind
        weights are configured, or for a kind the user didn't mention."""
        if not self.kind_weights:
            return 1.0
        return max(0.0, self.kind_weights.get(kind, 1.0))


# --- Per-signal scorers (each returns 0..1) -------------------------------------

def authority_score(s: SourceFeatures, weights: QualityWeights) -> float:
    dom, reg = s.domain, s.registrable
    if dom in weights.authority_overrides:
        return max(0.0, min(1.0, weights.authority_overrides[dom]))
    if reg in weights.authority_overrides:
        return max(0.0, min(1.0, weights.authority_overrides[reg]))
    if not dom:
        return 0.5  # non-web source (uploaded PDF, pasted text) — neutral, not penalized
    tld = dom.rsplit(".", 1)[-1] if "." in dom else ""
    score = 0.5
    if tld in _HIGH_AUTHORITY_TLDS or reg in _HIGH_AUTHORITY_DOMAINS:
        score = 0.95
    elif reg in _LOW_AUTHORITY_DOMAINS:
        score = 0.30
    # Platform-shaped sources (video, repo, discussion) are neutral on *authority* by
    # construction — authority is the wrong axis for them. They earn their place through
    # relevance, depth, and the KIND-diversity signal, not by being penalized here for
    # not being a .edu. Keep them at the 0.5 neutral baseline rather than lumping, e.g.,
    # a well-known Q&A/discussion host in with content-farm low authority.
    kind = classify_source_type(url=s.url, title=s.title)
    if kind in ("video", "repo", "discussion") and score < 0.5:
        score = 0.5
    if _SPAM_HINTS.search(dom):
        score = min(score, 0.25)
    return score


def depth_score(s: SourceFeatures, weights: QualityWeights) -> float:
    n = len(s.content or "")
    if n == 0:
        return 0.4  # unknown length (not fetched) — neutral-ish, don't over-reward thin
    # A bot-wall / sign-in shell can be long (tens of KB of nav chrome) but holds no
    # substance — don't let its length earn depth. Verified live: a blocked YouTube
    # import was 21k of navigation junk, a real transcript 144k; both would otherwise
    # score depth 1.0.
    if s.is_blocked:
        return 0.1
    # saturating curve: hits ~1.0 near depth_target_chars, never exceeds 1.
    return min(1.0, math.log1p(n) / math.log1p(weights.depth_target_chars))


def recency_score(s: SourceFeatures, weights: QualityWeights) -> float:
    if s.published is None:
        return 0.5  # undated — neutral
    now = datetime.now(timezone.utc)
    pub = s.published if s.published.tzinfo else s.published.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - pub).total_seconds() / 86400.0)
    # exponential decay with configurable half-life
    return 0.5 ** (age_days / max(1.0, weights.recency_halflife_days))


def relevance_score(s: SourceFeatures, question_terms: set[str],
                    expansions: dict[str, set[str]] | None = None) -> float:
    if not question_terms:
        return 0.5
    text = f"{s.title} {s.content}"
    terms = _content_terms(text)
    if not terms:
        return 0.0
    exp = expansions or {}
    # a question term is covered if the source contains it OR any of its expansions
    covered = 0
    for qt in question_terms:
        if qt in terms or (exp.get(qt) and (exp[qt] & terms)):
            covered += 1
    return covered / len(question_terms)


def domain_diversity_score(sources: list[SourceFeatures]) -> float:
    """Set-level: domain entropy normalized to 0..1. One source scores 0 (no diversity)."""
    if len(sources) <= 1:
        return 0.0
    counts = Counter(s.registrable or s.id for s in sources)
    n = sum(counts.values())
    entropy = -sum((c / n) * math.log2(c / n) for c in counts.values())
    max_entropy = math.log2(len(counts)) if len(counts) > 1 else 1.0
    # scale by how many distinct domains vs sources — punishes 8 sources / 1 domain
    distinct_ratio = len(counts) / len(sources)
    base = entropy / max_entropy if max_entropy > 0 else 0.0
    return max(0.0, min(1.0, base * distinct_ratio))


def type_diversity_score(sources: list[SourceFeatures],
                         kind_weights: dict[str, float] | None = None) -> float:
    """How many of the distinct research KINDS the set spans (paper/docs/repo/video/
    discussion/...), normalized by how many kinds a set this size could realistically
    cover. This is what makes "a paper + a repo + a video + a Reddit thread" rank as
    genuinely diverse while "six blogs on six domains" does not — the core NoteLab
    premise that a research library should be a *mix of kinds*, not just a spread of
    hostnames. One source scores 0.

    `kind_weights`: if the user has valued a kind at 0.0 (they don't care about it),
    that kind does not earn diversity credit — "diverse" means diverse across the kinds
    the user actually values, honoring the user-owns-value principle."""
    if len(sources) <= 1:
        return 0.0
    kw = kind_weights or {}

    def _valued(s: SourceFeatures) -> bool:
        # excluded if the user zeroed this kind out
        return kw.get(s.kind, 1.0) > 0 if kw else True

    # a blocked bot-wall shell doesn't contribute real variety — a "video" that's just
    # a sign-in page isn't a video source in any useful sense, so exclude blocked ones
    # from the kind set (verified live: Deep Research imports videos as blocked pages).
    kinds = {s.kind for s in sources if not s.is_blocked and _valued(s)}
    # the richest meaningfully-distinct palette we reward spanning (exclude the two
    # neutral fallbacks from the denominator so hitting them doesn't look like variety).
    rich_kinds = {k for k in kinds if k not in ("web",)}
    reachable = min(len(sources), len(SOURCE_TYPES) - 1)  # can't span more kinds than sources
    if reachable <= 0:
        return 0.0
    return max(0.0, min(1.0, len(rich_kinds) / reachable))


def diversity_score(sources: list[SourceFeatures], type_weight: float = 0.5,
                    kind_weights: dict[str, float] | None = None) -> float:
    """Composite set-level diversity: a blend of domain spread and research-kind spread.
    `type_weight` (0..1) sets how much the kind mix counts vs the domain mix; the default
    0.5 weights them equally. type_weight=0.0 reproduces the original domain-only score,
    so existing numbers move only when kind variety actually differs. `kind_weights`
    (optional) lets the user exclude kinds they don't value from the kind-spread term."""
    dom = domain_diversity_score(sources)
    if type_weight <= 0.0:
        return dom
    typ = type_diversity_score(sources, kind_weights=kind_weights)
    tw = max(0.0, min(1.0, type_weight))
    return max(0.0, min(1.0, (1.0 - tw) * dom + tw * typ))


# --- Composite ------------------------------------------------------------------

@dataclass
class SourceScore:
    id: str
    domain: str
    authority: float
    depth: float
    recency: float
    relevance: float
    kind: str = "web"
    blocked: bool = False

    def as_dict(self) -> dict:
        d = {
            "id": self.id, "domain": self.domain, "kind": self.kind,
            "authority": round(self.authority, 4), "depth": round(self.depth, 4),
            "recency": round(self.recency, 4), "relevance": round(self.relevance, 4),
        }
        if self.blocked:
            d["blocked"] = True
        return d


@dataclass
class QualityReport:
    quality_score: float                # composite 0..1 for the whole run
    diversity: float                    # set-level signal (domain + kind blend)
    n_sources: int
    per_source: list[SourceScore]
    signal_means: dict[str, float]      # mean of each per-source signal
    weights: dict[str, float]
    type_mix: dict[str, int] = field(default_factory=dict)        # kind -> count
    type_diversity: float = 0.0         # kind-spread sub-signal (0..1)
    domain_diversity: float = 0.0       # domain-spread sub-signal (0..1)

    def as_dict(self) -> dict:
        return {
            "quality_score": round(self.quality_score, 4),
            "diversity": round(self.diversity, 4),
            "type_diversity": round(self.type_diversity, 4),
            "domain_diversity": round(self.domain_diversity, 4),
            "type_mix": dict(self.type_mix),
            "n_sources": self.n_sources,
            "signal_means": {k: round(v, 4) for k, v in self.signal_means.items()},
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "per_source": [s.as_dict() for s in self.per_source],
        }


def score_sources(
    sources: list[SourceFeatures],
    question: str = "",
    weights: QualityWeights | None = None,
) -> QualityReport:
    """Score a research run's source set. This is the function A/B ranking calls."""
    w = (weights or QualityWeights()).normalized()
    qterms = _content_terms(question)

    per_source: list[SourceScore] = []
    for s in sources:
        per_source.append(SourceScore(
            id=s.id,
            domain=s.domain or "(non-web)",
            authority=authority_score(s, w),
            depth=depth_score(s, w),
            recency=recency_score(s, w),
            relevance=relevance_score(s, qterms, w.term_expansions),
            kind=s.kind,
            blocked=s.is_blocked,
        ))

    n = len(per_source)
    if n == 0:
        return QualityReport(0.0, 0.0, 0, [], {}, _weights_dict(w),
                             type_mix={}, type_diversity=0.0, domain_diversity=0.0)

    # Per-KIND value weighting: each source's signals contribute in proportion to how
    # much the user values its kind (kind_weight). With no kind_weights configured
    # every weight is 1.0, so this reduces exactly to a plain mean. A kind weighted
    # 0.0 drops out of the score entirely. The denominator is the sum of kind weights
    # (not n) so the weighted mean stays in 0..1.
    kw = [w.kind_weight(s.kind) for s in sources]
    wsum = sum(kw)
    if wsum <= 0:
        # user zeroed out every kind present — nothing they value is here.
        mean_auth = mean_depth = mean_rec = mean_rel = 0.0
    else:
        mean_auth = sum(p.authority * k for p, k in zip(per_source, kw)) / wsum
        mean_depth = sum(p.depth * k for p, k in zip(per_source, kw)) / wsum
        mean_rec = sum(p.recency * k for p, k in zip(per_source, kw)) / wsum
        mean_rel = sum(p.relevance * k for p, k in zip(per_source, kw)) / wsum
    div = diversity_score(sources, type_weight=w.diversity_type_weight,
                          kind_weights=w.kind_weights)

    composite = (
        w.authority * mean_auth
        + w.depth * mean_depth
        + w.recency * mean_rec
        + w.relevance * mean_rel
        + w.diversity * div
    )
    return QualityReport(
        quality_score=composite,
        diversity=div,
        n_sources=n,
        per_source=per_source,
        signal_means={
            "authority": mean_auth, "depth": mean_depth,
            "recency": mean_rec, "relevance": mean_rel, "diversity": div,
        },
        weights=_weights_dict(w),
        type_mix=dict(Counter(p.kind for p in per_source)),
        type_diversity=type_diversity_score(sources, kind_weights=w.kind_weights),
        domain_diversity=domain_diversity_score(sources),
    )


def _weights_dict(w: QualityWeights) -> dict[str, float]:
    return {
        "authority": w.authority, "diversity": w.diversity,
        "recency": w.recency, "depth": w.depth, "relevance": w.relevance,
    }


# --- Named weight presets -------------------------------------------------------
# Ready-to-use profiles for common research modes. Pass one to score_sources /
# run_experiment as `weights`, or start from one and override. Documented in
# docs/CONFIG.md. Weights need not sum to 1 (they are normalized internally).

PRESETS: dict[str, QualityWeights] = {
    # News/current-events: recency and authority dominate; depth matters least.
    "news": QualityWeights(authority=0.30, recency=0.35, diversity=0.15,
                           relevance=0.15, depth=0.05, recency_halflife_days=30.0),
    # Literature review: depth + diversity + authority; recency nearly irrelevant.
    "lit_review": QualityWeights(authority=0.30, depth=0.30, diversity=0.25,
                                 relevance=0.15, recency=0.0,
                                 recency_halflife_days=1825.0, depth_target_chars=8000),
    # Technical docs / API: authority + depth + relevance; diversity less useful
    # (the canonical doc is what you want, not many opinions).
    "docs": QualityWeights(authority=0.35, depth=0.30, relevance=0.25,
                           diversity=0.05, recency=0.05),
    # Academic-only: the user values papers and official docs; forums, videos and
    # random blogs are discounted hard. Demonstrates the "user owns what they value"
    # principle — nothing here says papers are globally "better", it's this user's call.
    "academic": QualityWeights(
        authority=0.35, depth=0.30, diversity=0.10, relevance=0.20, recency=0.05,
        recency_halflife_days=1825.0, depth_target_chars=8000,
        kind_weights={"paper": 1.0, "docs": 0.8, "reference": 0.5,
                      "repo": 0.4, "web": 0.3, "video": 0.1, "discussion": 0.1}),
    # Forum-first / learn-by-community: the user values discussion and step-by-step
    # videos above papers (e.g. learning a tool, debugging a real-world setup).
    "forum_first": QualityWeights(
        authority=0.10, depth=0.15, diversity=0.25, relevance=0.25, recency=0.25,
        recency_halflife_days=180.0, diversity_type_weight=0.7,
        kind_weights={"discussion": 1.0, "video": 0.9, "docs": 0.7, "repo": 0.6,
                      "web": 0.5, "paper": 0.4, "reference": 0.4}),
    # Starting a project: you want a *diverse mix of kinds* — papers for the theory,
    # docs/repos to build on, step-by-step videos, and community discussion for the
    # gotchas. Diversity dominates and leans hard on KIND spread; recency matters
    # (tooling moves) but authority is relaxed so videos/discussion aren't buried.
    "project_kickstart": QualityWeights(
        authority=0.15, diversity=0.30, recency=0.20, relevance=0.20, depth=0.15,
        diversity_type_weight=0.8, recency_halflife_days=540.0),
    # Balanced default (same as QualityWeights() defaults, named for discoverability).
    "balanced": QualityWeights(),
}


def preset(name: str) -> QualityWeights:
    """Return a named weight preset. Raises KeyError with the valid names on a miss."""
    if name not in PRESETS:
        raise KeyError(f"unknown preset {name!r}; choose from {sorted(PRESETS)}")
    return PRESETS[name]
