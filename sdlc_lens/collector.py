"""One passive collection pass. The source repository is never modified."""

from __future__ import annotations

from pathlib import Path

from .discovery import discover, git_head
from .parser import parse
from .store import Store


def collect(root: Path, store: Store, repository: str = "touristico") -> dict:
    head = git_head(root)
    found, warnings = discover(root, head)
    transient = [w for w in warnings if "changed while reading" in w or "cannot read UTF-8" in w]
    if transient:
        raise RuntimeError("snapshot incomplete; retry without publishing: " + "; ".join(transient))
    if git_head(root) != head:
        raise RuntimeError("Git HEAD changed during scan; retry without publishing")
    parsed = {artifact.path: parse(artifact) for artifact in found}
    return store.collect(repository, head, found, parsed, warnings)
