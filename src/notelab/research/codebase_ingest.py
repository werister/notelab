"""
codebase_ingest.py — ingest actual code from GitHub repos, not just READMEs.

The core problem: NotebookLM indexes a GitHub repo's README and a few key files,
but the actual implementation code (functions, classes, tests, config) is invisible
to the research engine. This module closes that gap by:

  1. Fetching the repo via the GitHub API (or walking a local path)
  2. Extracting code structure: functions, classes, imports, type signatures
  3. Building a "code digest" — a compact representation of the codebase whose
     `to_text()` markdown can be added as a NotebookLM source alongside the README
  4. Scoring code quality signals: test-file ratio, doc coverage

The output is a `CodebaseDigest` that can be:
  - Added to a NotebookLM notebook as a source (via nlmx_adapter)
  - Scored by quality.py (with code-specific signals)
  - Used by evolve.py to make better curation decisions

Usage:
    from notelab.research.codebase_ingest import CodebaseIngester

    ingester = CodebaseIngester()
    digest = ingester.ingest("https://github.com/user/repo")
    # digest.to_text() returns a markdown summary for NotebookLM
    # digest.to_source_features() returns a SourceFeatures for the ledger
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse


@dataclass
class CodeFile:
    """One source file extracted from a codebase."""

    path: str
    language: str
    content: str
    lines: int = 0
    # Extracted structure
    functions: List[Dict[str, Any]] = field(default_factory=list)
    classes: List[Dict[str, Any]] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    # Quality signals
    has_tests: bool = False
    has_docstrings: bool = False
    comment_ratio: float = 0.0

    def __post_init__(self):
        if not self.lines:
            self.lines = len(self.content.splitlines())


@dataclass
class CodebaseDigest:
    """A compact representation of a codebase for research ingestion."""

    repo_url: str
    repo_name: str
    default_branch: str = "main"
    # Structure
    files: List[CodeFile] = field(default_factory=list)
    languages: Dict[str, int] = field(default_factory=dict)  # language -> file count
    total_lines: int = 0
    # Quality signals
    test_file_ratio: float = 0.0  # fraction of ingested files that are tests
    doc_coverage: float = 0.0  # fraction of files with docstrings
    # Metadata
    ingested_at: float = field(default_factory=time.time)
    ingest_duration_ms: float = 0.0

    def to_text(self) -> str:
        """Render as markdown for NotebookLM ingestion.

        This produces a compact but information-dense summary that captures
        the codebase's structure, key APIs, and quality signals — everything
        a research question about "how does X work" or "what's the architecture"
        would need.
        """
        lines = [
            f"# Codebase: {self.repo_name}",
            "",
            f"**Repository:** {self.repo_url}",
            f"**Default branch:** {self.default_branch}",
            f"**Total files:** {len(self.files)}",
            f"**Total lines:** {self.total_lines:,}",
            "",
            "## Languages",
            "",
        ]
        for lang, count in sorted(self.languages.items(), key=lambda x: -x[1]):
            lines.append(f"- {lang}: {count} files")

        lines.extend(["", "## Quality Signals", ""])
        lines.append(f"- Test files: {self.test_file_ratio:.0%} of ingested files")
        lines.append(f"- Docstring coverage: {self.doc_coverage:.0%}")

        # Key files (most important first: README, main entry points, tests)
        key_files = [f for f in self.files if self._is_key_file(f)]
        if key_files:
            lines.extend(["", "## Key Files", ""])
            for f in key_files[:10]:
                lines.append(f"### `{f.path}` ({f.lines} lines)")
                lines.append("")
                # Show structure summary
                if f.classes:
                    lines.append("**Classes:**")
                    for c in f.classes[:5]:
                        methods = ", ".join(c.get("methods", [])[:5])
                        lines.append(f"- `{c['name']}`({methods})")
                    lines.append("")
                if f.functions:
                    lines.append("**Functions:**")
                    for fn in f.functions[:8]:
                        sig = fn.get("signature", fn["name"])
                        lines.append(f"- `{sig}`")
                    lines.append("")
                if f.imports:
                    lines.append(f"**Imports:** {', '.join(f.imports[:10])}")
                    lines.append("")
            if len(key_files) > 10:
                lines.extend(["", f"*+ {len(key_files) - 10} more key files not shown*"])

        # API surface (all public functions/classes across all files)
        api_surface = self._api_surface()
        if api_surface:
            lines.extend(["", "## API Surface", ""])
            for item in api_surface[:30]:
                lines.append(f"- `{item}`")
            if len(api_surface) > 30:
                lines.append(f"- ... + {len(api_surface) - 30} more")

        return "\n".join(lines)

    def to_source_features(self) -> Dict[str, Any]:
        """Convert to a dict that can be stored as a ledger source."""
        return {
            "id": f"codebase-{self.repo_name}",
            "title": f"Codebase: {self.repo_name}",
            "url": self.repo_url,
            "domain": urlparse(self.repo_url).netloc,
            "content": self.to_text(),
            "published": None,
            "languages": self.languages,
            "total_lines": self.total_lines,
            "test_file_ratio": self.test_file_ratio,
            "doc_coverage": self.doc_coverage,
        }

    def _is_key_file(self, f: CodeFile) -> bool:
        """Determine if a file is 'key' (important for understanding the codebase)."""
        path_lower = f.path.lower()
        return (
            "readme" in path_lower
            or "setup" in path_lower
            or "pyproject" in path_lower
            or "main" in path_lower
            or "index" in path_lower
            or "test" in path_lower
            or "spec" in path_lower
        )

    def _api_surface(self) -> List[str]:
        """Extract all public function/class signatures across the codebase."""
        api = []
        for f in self.files:
            for c in f.classes:
                if not c["name"].startswith("_"):
                    api.append(f"{f.path}::{c['name']}")
            for fn in f.functions:
                if not fn["name"].startswith("_"):
                    api.append(f"{f.path}::{fn['name']}")
        return api


# Language detection by extension
_EXTENSION_TO_LANGUAGE = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".c": "C",
    ".cpp": "C++",
    ".h": "C",
    ".hpp": "C++",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".sh": "Shell",
    ".sql": "SQL",
    ".html": "HTML",
    ".css": "CSS",
    ".md": "Markdown",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".xml": "XML",
}

# Files to skip (generated, vendored, dependencies, environments)
_SKIP_PATTERNS = re.compile(
    r"(node_modules|\.git/|vendor/|dist/|build/|\.venv|venv/|\.tox|\.mypy_cache"
    r"|\.pytest_cache|\.ruff_cache|__pycache__|\.egg-info|\.serena|worktrees/"
    r"|\.pyc$|\.pyo$|\.so$|\.dll$|\.exe$|\.png$|\.jpg$|\.gif$|\.ico$|\.woff2?$|\.ttf$)"
)

# Extensions worth ingesting: known code plus lightweight docs/config.
_TEXT_EXTENSIONS = frozenset(_EXTENSION_TO_LANGUAGE) | frozenset(
    {".txt", ".rst", ".cfg", ".ini", ".toml", ".md"}
)

_USER_AGENT = "notelab-codebase-ingest/1.0 (+https://github.com/werister/notelab)"


def _detect_language(path: str) -> str:
    ext = Path(path).suffix.lower()
    return _EXTENSION_TO_LANGUAGE.get(ext, "Other")


def _should_skip(path: str) -> bool:
    if _SKIP_PATTERNS.search(path):
        return True
    # Extension gate: split on the last dot of the basename.
    base = path.rsplit("/", 1)[-1]
    dot = base.rfind(".")
    suffix = base[dot:].lower() if dot > 0 else ""
    if suffix and suffix not in _TEXT_EXTENSIONS:
        return True
    return False


def _extract_structure(content: str, language: str):
    """Return (functions, classes, imports) for supported languages."""
    if language == "Python":
        return (
            _extract_functions_python(content),
            _extract_classes_python(content),
            _extract_imports_python(content),
        )
    return [], [], []


def _extract_functions_python(content: str) -> List[Dict[str, Any]]:
    """Extract function signatures from Python source."""
    functions = []
    # Match: def name(args) -> type:
    pattern = re.compile(
        r"^def\s+(\w+)\s*\(([^)]*)\)\s*(?:->\s*([^:]+))?\s*:",
        re.MULTILINE,
    )
    for m in pattern.finditer(content):
        name, args, ret_type = m.group(1), m.group(2).strip(), m.group(3)
        sig = f"{name}({args})"
        if ret_type:
            sig += f" -> {ret_type.strip()}"
        functions.append({"name": name, "signature": sig})
    return functions


def _extract_classes_python(content: str) -> List[Dict[str, Any]]:
    """Extract class definitions from Python source."""
    classes = []
    pattern = re.compile(r"^class\s+(\w+)(?:\(([^)]*)\))?\s*:", re.MULTILINE)
    for m in pattern.finditer(content):
        name, bases = m.group(1), m.group(2) or ""
        # Find methods within this class (simplified: just look for indented defs)
        class_start = m.start()
        # Find the next class or end of file
        next_class = re.search(r"^class\s+\w+", content[class_start + 1:], re.MULTILINE)
        class_end = class_start + 1 + next_class.start() if next_class else len(content)
        class_body = content[class_start:class_end]
        methods = [
            m2.group(1)
            # [ \t]+, not \s+: \s matches newlines, which would also match
            # module-level defs preceded by a blank line.
            for m2 in re.finditer(r"^[ \t]+def\s+(\w+)\s*\(", class_body, re.MULTILINE)
        ]
        classes.append({"name": name, "bases": bases, "methods": methods})
    return classes


def _extract_imports_python(content: str) -> List[str]:
    """Extract import statements from Python source."""
    imports = []
    for m in re.finditer(r"^(?:from\s+(\S+)\s+)?import\s+(.+)$", content, re.MULTILINE):
        module = m.group(1) or ""
        names = m.group(2).strip()
        imports.append(f"{module} {names}".strip())
    return imports


def _has_docstring(content: str, start_pos: int) -> bool:
    """Check if a function/class at start_pos has a docstring."""
    # Look for a string literal on the next non-empty line
    rest = content[start_pos:]
    lines = rest.splitlines()
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        return stripped.startswith('"""') or stripped.startswith("'''")
    return False


def _comment_ratio(content: str) -> float:
    """Estimate the ratio of comment lines to total lines."""
    lines = content.splitlines()
    if not lines:
        return 0.0
    comment_lines = sum(
        1 for line in lines if line.strip().startswith(("#", "//", "/*", "*"))
    )
    return comment_lines / len(lines)


class CodebaseIngester:
    """Ingest a GitHub repo (or local path) into a CodebaseDigest.

    Extracts code structure, key APIs, and quality signals — everything a
    research question about "how does X work" or "what's the architecture"
    would need, without the noise of generated files and dependencies.
    """

    def __init__(self, max_files: int = 200, max_file_lines: int = 500):
        self.max_files = max_files
        self.max_file_lines = max_file_lines

    def ingest(self, repo_url: str) -> CodebaseDigest:
        """Ingest a repo and return a CodebaseDigest.

        Args:
            repo_url: A GitHub URL (https://github.com/user/repo) or local path.

        Returns:
            CodebaseDigest with files, languages, and quality signals.
        """
        t0 = time.time()

        if repo_url.startswith("http"):
            files, branch = self._fetch_remote(repo_url)
        else:
            files = self._walk_local(Path(repo_url))
            branch = "local"

        # Build digest
        languages: Dict[str, int] = {}
        total_lines = 0
        test_files = 0
        doc_files = 0

        for f in files:
            lang = f.language
            languages[lang] = languages.get(lang, 0) + 1
            total_lines += f.lines
            if "test" in f.path.lower() or "spec" in f.path.lower():
                test_files += 1
            if f.has_docstrings:
                doc_files += 1

        n_files = len(files)
        test_file_ratio = test_files / n_files if n_files else 0.0
        doc_coverage = doc_files / n_files if n_files else 0.0

        duration_ms = (time.time() - t0) * 1000.0

        repo_name = repo_url.rstrip("/").split("/")[-1]
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]

        return CodebaseDigest(
            repo_url=repo_url,
            repo_name=repo_name,
            default_branch=branch,
            files=files,
            languages=languages,
            total_lines=total_lines,
            test_file_ratio=round(test_file_ratio, 4),
            doc_coverage=round(doc_coverage, 4),
            ingested_at=time.time(),
            ingest_duration_ms=round(duration_ms, 1),
        )

    def _fetch_remote(self, repo_url: str) -> Tuple[List[CodeFile], str]:
        """Fetch repo files via GitHub API (no git clone needed).

        Uses the git trees API to list files, then fetches each file's content.
        Skips files that are too large or match skip patterns.
        Returns (files, default_branch).
        """
        # Extract owner/repo from URL
        parsed = urlparse(repo_url)
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 2:
            raise ValueError(f"Invalid GitHub URL: {repo_url}")
        owner, repo = parts[0], parts[1]

        # Get default branch
        branch = self._get_default_branch(owner, repo)

        # Get file tree
        tree_url = (
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}"
            f"?recursive=1"
        )
        req = urllib.request.Request(tree_url, headers={
            "Accept": "application/json", "User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            tree_data = json.loads(resp.read().decode("utf-8"))

        blobs = [i for i in tree_data.get("tree", []) if i.get("type") == "blob"]
        # Code first: with a max_files cap, API order would otherwise spend the
        # budget on alphabetically-early docs/config.
        blobs.sort(key=lambda i: (
            0 if _detect_language(i["path"]) not in ("Other", "Markdown") else 1,
            i["path"],
        ))
        files: List[CodeFile] = []
        for item in blobs:
            path = item["path"]
            if _should_skip(path):
                continue
            if len(files) >= self.max_files:
                break

            # Fetch file content
            raw_url = (
                f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
            )
            try:
                req = urllib.request.Request(
                    raw_url, headers={"Accept": "text/plain", "User-Agent": _USER_AGENT}
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    content = resp.read().decode("utf-8", errors="replace")
            except Exception:
                continue  # skip files that fail to fetch

            lines = content.splitlines()
            if len(lines) > self.max_file_lines:
                content = "\n".join(lines[: self.max_file_lines])
                lines = lines[: self.max_file_lines]

            lang = _detect_language(path)
            functions, classes, imports = _extract_structure(content, lang)

            files.append(
                CodeFile(
                    path=path,
                    language=lang,
                    content=content,
                    lines=len(lines),
                    functions=functions,
                    classes=classes,
                    imports=imports,
                    has_tests="test" in path.lower() or "spec" in path.lower(),
                    has_docstrings=self._check_docstrings(content, lang),
                    comment_ratio=round(_comment_ratio(content), 4),
                )
            )

        return files, branch

    def _walk_local(self, root: Path) -> List[CodeFile]:
        """Walk a local directory and extract code files (sorted for determinism)."""
        files: List[CodeFile] = []
        for path in sorted(root.rglob("*"), key=str):
            if not path.is_file():
                continue
            rel_path = str(path.relative_to(root))
            if _should_skip(rel_path):
                continue
            if len(files) >= self.max_files:
                break

            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            lines = content.splitlines()
            if len(lines) > self.max_file_lines:
                content = "\n".join(lines[: self.max_file_lines])
                lines = lines[: self.max_file_lines]

            lang = _detect_language(rel_path)
            functions, classes, imports = _extract_structure(content, lang)

            files.append(
                CodeFile(
                    path=rel_path,
                    language=lang,
                    content=content,
                    lines=len(lines),
                    functions=functions,
                    classes=classes,
                    imports=imports,
                    has_tests="test" in rel_path.lower() or "spec" in rel_path.lower(),
                    has_docstrings=self._check_docstrings(content, lang),
                    comment_ratio=round(_comment_ratio(content), 4),
                )
            )

        return files

    def _get_default_branch(self, owner: str, repo: str) -> str:
        """Get the default branch for a GitHub repo."""
        url = f"https://api.github.com/repos/{owner}/{repo}"
        req = urllib.request.Request(url, headers={
            "Accept": "application/json", "User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("default_branch", "main")

    def _check_docstrings(self, content: str, language: str) -> bool:
        """Check if a file has docstrings/comments for its main definitions."""
        if language == "Python":
            # Check for docstrings after class/def
            return bool(
                re.search(r'""".*?"""', content, re.DOTALL)
                or re.search(r"'''.*?'''", content, re.DOTALL)
            )
        elif language in ("JavaScript", "TypeScript"):
            # Check for JSDoc comments
            return bool(re.search(r"/\*\*.*?\*/", content, re.DOTALL))
        elif language == "Go":
            # Check for comments before func declarations
            return bool(re.compile(r"//.*\nfunc ").search(content))
        return False
