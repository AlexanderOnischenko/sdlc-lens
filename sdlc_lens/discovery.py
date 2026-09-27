"""Read-only discovery and content snapshots for Touristico-style artifacts."""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


SUFFIXES = (
    (".development-cycle.state.md", "controller"),
    (".review-fix-orchestration.md", "review_delivery"),
    (".delivery-orchestration.md", "delivery"),
    (".law-test-matrix.md", "matrix"),
    (".reverse-law-audit.md", "reverse"),
    (".micro-guarantees.md", "guarantees"),
    (".guarantee-bugs.md", "bugs"),
    (".law-suites.md", "laws"),
    (".use-case-law-trace.md", "law_trace"),
)
SOURCE_HEADER = re.compile(r"(?im)^\s*[-*]?\s*(?:\*\*|`)?Source(?: spec)?(?:\*\*|`)?\s*:\s*(?:\*\*|`)?\s*(.+?)\s*$")


@dataclass(frozen=True)
class Artifact:
    path: str
    kind: str
    feature_key: str
    content: str
    sha256: str
    git_blob: str | None
    dirty: bool


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_head(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def git_blob(root: Path, relpath: str, revision: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", f"{revision}:{relpath}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def content_git_blob(root: Path, raw: bytes) -> str | None:
    """Hash the bytes we actually stored, not a second read of the worktree."""
    result = subprocess.run(
        ["git", "-C", str(root), "hash-object", "--stdin"],
        input=raw,
        capture_output=True,
        check=False,
    )
    return result.stdout.decode("ascii").strip() if result.returncode == 0 else None


def classify(path: Path) -> tuple[str, Path] | None:
    name = path.name
    if path.as_posix() == "docs/development/process-guards.md":
        return "project_guards", path
    for suffix, kind in SUFFIXES:
        if name.endswith(suffix):
            return kind, path.with_name(name[: -len(suffix)] + ".md")
    if name.endswith(".md") and path.parts[0] == "docs" and (
        name in {"spec.md", "refined_feature_spec.md", "auth.md", "backend_manifest.md"}
        or "stories" in path.parts
    ):
        return "source", path
    return None


def _source_ref(text: str, fallback: Path) -> Path:
    def normalize(path: Path) -> Path:
        if path.name.endswith(".micro-guarantees.md"):
            return path.with_name(path.name[: -len(".micro-guarantees.md")] + ".md")
        return path

    match = SOURCE_HEADER.search(text[:3000])
    if not match:
        for line in text[:3000].splitlines():
            value = line.lstrip("-* ").replace("**", "").replace("`", "")
            if value.lower().startswith("source spec:") or value.lower().startswith("source:"):
                value = value.split(":", 1)[1].strip()
                break
        else:
            return normalize(fallback)
    else:
        value = match.group(1).strip().strip("* `")
    value = value.split(";")[0].strip("* `")
    if not value.endswith(".md") or "://" in value:
        return normalize(fallback)
    ref = Path(value)
    if ref.is_absolute() or ".." in ref.parts:
        return normalize(fallback)
    resolved = ref if ref.parts and ref.parts[0] == "docs" else fallback.parent / ref
    return normalize(resolved)


def discover(root: Path, head: str | None = None) -> tuple[list[Artifact], list[str]]:
    root = root.resolve()
    head = head if head is not None else git_head(root)
    docs = root / "docs"
    if not docs.is_dir():
        raise ValueError(f"No docs directory in {root}")
    artifacts: list[Artifact] = []
    warnings: list[str] = []
    for path in sorted(docs.rglob("*.md")):
        if not path.resolve().is_relative_to(root):
            warnings.append(f"{path}: symlink escapes repository; skipped")
            continue
        relative = path.relative_to(root)
        spec = classify(relative)
        if spec is None:
            continue
        kind, fallback = spec
        try:
            before = path.stat()
            raw = path.read_bytes()
            after = path.stat()
            if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                warnings.append(f"{relative}: changed while reading; skipped until next scan")
                continue
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"{relative}: cannot read UTF-8: {exc}")
            continue
        feature = "__project__" if kind == "project_guards" else _source_ref(text, fallback).as_posix()
        rel = relative.as_posix()
        blob = git_blob(root, rel, head) if head else None
        # Git's blob hash uses a different algorithm from our content SHA-256.
        # Equality must be checked with git hash-object, never by comparing them.
        dirty = True
        if blob:
            dirty = content_git_blob(root, raw) != blob
        artifacts.append(Artifact(rel, kind, feature, text, sha256(raw), blob, dirty))
    return artifacts, warnings
