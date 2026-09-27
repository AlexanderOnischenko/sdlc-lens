"""Read-only projections. Unknown is explicit; no inferred convergence or costs."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
import re


FINGERPRINT_KIND = {
    "Source spec": "source", "MG contract": "guarantees", "LS/LP inventory": "laws",
    "Matrix": "matrix", "Reverse audit": "reverse",
}
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")


def _title(artifact: dict[str, Any]) -> str:
    content = artifact.get("content", "")
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return Path(artifact["path"]).stem


def features(current: dict[str, Any], contents: dict[str, str] | None = None) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"artifacts": [], "entities": []})
    for artifact in current["artifacts"]:
        if artifact["feature_key"] != "__project__":
            grouped[artifact["feature_key"]]["artifacts"].append(artifact)
    for entity in current["entities"]:
        if entity["feature_key"] != "__project__":
            grouped[entity["feature_key"]]["entities"].append(entity)
    output = []
    for key, group in grouped.items():
        artifacts = group["artifacts"]
        source = next((a for a in artifacts if a["kind"] == "source" and a["path"] == key), None)
        controller = next((a for a in artifacts if a["kind"] == "controller"), None)
        title = Path(key).parent.name.replace("_", " ")
        if source and source["metadata"].get("title"):
            title = source["metadata"]["title"]
        elif source and contents and source["id"] in contents:
            title = _title({"path": source["path"], "content": contents[source["id"]]})
        elif source:
            title = Path(key).stem.replace("_", " ")
        kinds = Counter(e["kind"] for e in group["entities"])
        lc_status = Counter(e["status"] or "UNKNOWN" for e in group["entities"] if e["kind"] == "LC")
        bug_status = Counter(e["status"] or "UNKNOWN" for e in group["entities"] if e["kind"] == "BG")
        controller_meta = controller["metadata"] if controller else {}
        fingerprint_checks = []
        if controller:
            for label, kind in FINGERPRINT_KIND.items():
                declared = controller["fingerprints"].get(label)
                actual = next((a for a in artifacts if a["kind"] == kind), None)
                match = HASH_RE.search(declared or "")
                if declared and actual and match:
                    fingerprint_checks.append({"artifact": kind, "declared": match.group(0),
                                               "observed": actual["content_hash"],
                                               "matches": match.group(0) == actual["content_hash"]})
        quality = []
        if not source:
            quality.append("source_missing")
        if not controller:
            quality.append("no_controller_state")
        if any(a["dirty"] for a in artifacts):
            quality.append("uncommitted_artifacts")
        if any(a["warnings"] for a in artifacts):
            quality.append("parse_warnings")
        if any(not check["matches"] for check in fingerprint_checks):
            quality.append("artifact_fingerprint_mismatch")
        quality.append("pass_history_partial")
        quality.append("usage_unknown")
        if all(a["kind"] == "source" for a in artifacts):
            continue
        output.append({
            "key": key, "title": title, "declared_status": controller_meta.get("cycle status"),
            "phase": controller_meta.get("current phase"),
            "generation": controller_meta.get("current forward generation"),
            "resume_point": controller_meta.get("last safe resume point"),
            "readiness": "not_independently_verified",
            "counts": dict(kinds), "lc_status": dict(lc_status), "bug_status": dict(bug_status),
            "artifact_count": len(artifacts), "quality": quality,
            "fingerprint_checks": fingerprint_checks,
        })
    return sorted(output, key=lambda item: item["key"])


def feature(current: dict[str, Any], key: str, contents: dict[str, str] | None = None) -> dict[str, Any] | None:
    summary = next((item for item in features(current, contents) if item["key"] == key), None)
    if not summary:
        return None
    return {
        **summary,
        "artifacts": sorted((a for a in current["artifacts"] if a["feature_key"] == key), key=lambda a: a["path"]),
        "entities": sorted((e for e in current["entities"] if e["feature_key"] == key),
                           key=lambda e: (e["kind"], e["entity_id"])),
        "observed_at": current["scan"]["observed_at"] if current["scan"] else None,
        "last_checked_at": current["scan"]["last_checked_at"] if current["scan"] else None,
    }
