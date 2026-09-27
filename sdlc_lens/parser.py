"""Versioned, conservative parsers for existing Markdown artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from .discovery import Artifact


PARSER_VERSION = "markdown-v4"
ID_RE = re.compile(r"\b(MG|LS|LP|LC|BG|NF|RV|PG(?:-PROJ)?|FI|WI|RF|IV)-([0-9]{1,5})(?![0-9])")
RANGE_RE = re.compile(r"\b(MG|LS|LP|LC|BG|NF|RV|PG|FI|WI|RF|IV)-([0-9]{1,5})\s*(?:–|—|-|\.\.)\s*(?:(?:\1)-)?([0-9]{1,5})\b")
INLINE_RE = re.compile(r"<[^>]+>|\[([^]]+)\]\([^)]*\)|[`*]")
HEADING_RE = re.compile(r"^(#{2,5})\s+((?:MG|LS|LP|LC|BG|NF|RV|PG(?:-PROJ)?|FI|WI|RF|IV)-\d+)\s*(?:[—:-]\s*(.*))?$")
META_KEYS = {
    "source spec", "source", "branch", "git head", "cycle status", "current phase",
    "current forward generation", "last safe resume point", "generation", "trigger",
    "mg fingerprint", "ls fingerprint", "matrix", "open work", "code baseline",
    "audit mode", "status", "baseline", "contract fingerprint", "code-surface fingerprint",
    "matrix fingerprint", "forward generation", "reverse pass within generation",
    "total executed reverse passes", "total invocation attempts", "last updated by",
}
TITLE_COLUMNS = {
    "MG": ("Guarantee", "Title", "Truth"),
    "LS": ("Law", "Title"),
    "LP": ("Path", "State", "Title"),
    "LC": ("Path / state", "Path/state", "Expected outcome", "Condition"),
    "BG": ("Current defect", "Summary", "Title", "Bug"),
    "NF": ("Protected truth", "Raw ref", "Source"),
    "RV": ("Class", "Title"),
    "PG": ("Generalized challenge", "Challenge"),
    "FI": ("Finding", "Title", "Summary"),
    "WI": ("Work item", "Title"),
    "RF": ("Finding", "Title"),
    "IV": ("Code surface", "Dimension"),
}
OWNER_KIND = {
    "MG": "guarantees", "LS": "laws", "LP": "laws", "LC": "matrix",
    "BG": "bugs", "NF": "controller", "RV": "reverse", "PG": "controller",
    "FI": "delivery", "WI": "delivery", "RF": "review_delivery", "IV": "reverse",
}


@dataclass(frozen=True)
class ParsedEntity:
    kind: str
    entity_id: str
    title: str
    status: str | None
    line: int
    section: str
    fields: dict[str, str]
    refs: list[dict[str, str]]
    source: str  # table or heading


@dataclass(frozen=True)
class ParsedArtifact:
    entities: list[ParsedEntity]
    metadata: dict[str, str]
    counters: dict[str, int]
    fingerprints: dict[str, str]
    warnings: list[str]


def clean(text: str) -> str:
    text = INLINE_RE.sub(lambda m: m.group(1) or "", text)
    return " ".join(text.split())


def references(text: str, own_id: str | None = None) -> list[dict[str, str]]:
    found: set[tuple[str, str]] = set()
    for match in RANGE_RE.finditer(text):
        prefix, first, last = match.groups()
        begin, end = int(first), int(last)
        if end < begin or end - begin > 500:
            continue
        for number in range(begin, end + 1):
            found.add((prefix, f"{prefix}-{number:0{max(len(first), len(last))}d}"))
    for match in ID_RE.finditer(text):
        prefix, number = match.groups()
        found.add(("PG" if prefix == "PG-PROJ" else prefix, f"{prefix}-{number}"))
    if own_id:
        found.discard(("PG" if own_id.startswith("PG-PROJ-") else own_id.split("-")[0], own_id))
    return [{"kind": kind, "id": entity_id, "confidence": "text_reference"}
            for kind, entity_id in sorted(found)]


def split_table(line: str) -> list[str]:
    body = line.strip()
    if not body.startswith("|") or not body.endswith("|"):
        return []
    body = body[1:-1]
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in body:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append(clean("".join(current).strip()))
            current = []
        else:
            current.append(char)
    cells.append(clean("".join(current).strip()))
    return cells


def _separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", c) for c in cells)


def _metadata(line: str) -> tuple[str, str] | None:
    if not line.startswith(("- ", "* ")):
        return None
    value = line[2:].strip()
    value = value.replace("**", "").replace("`", "")
    if ":" not in value:
        return None
    key, raw = value.split(":", 1)
    key = clean(key).lower()
    if key not in META_KEYS:
        return None
    return key, clean(raw)


def _id_from_cell(value: str) -> tuple[str, str] | None:
    match = ID_RE.fullmatch(value)
    if not match:
        return None
    return ("PG" if match.group(1) == "PG-PROJ" else match.group(1)), match.group(0)


def parse(artifact: Artifact) -> ParsedArtifact:
    lines = artifact.content.splitlines()
    entities: dict[tuple[str, str], ParsedEntity] = {}
    metadata: dict[str, str] = {}
    counters: dict[str, int] = {}
    fingerprints: dict[str, str] = {}
    warnings: list[str] = []
    section = ""
    headers: list[str] | None = None
    in_fence = False
    current_detail: tuple[str, str] | None = None
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("# ") and "title" not in metadata:
            metadata["title"] = clean(stripped[2:])
        if stripped.startswith("```"):
            in_fence = not in_fence
            headers = None
            continue
        if in_fence:
            continue
        if stripped.startswith("#"):
            headers = None
            current_detail = None
            section = clean(stripped.lstrip("# "))
            match = HEADING_RE.match(stripped)
            if match:
                kind, entity_id = _id_from_cell(match.group(2)) or (None, None)
                if kind and (kind, entity_id) not in entities:
                    entities[(kind, entity_id)] = ParsedEntity(
                        kind, entity_id, clean(match.group(3) or section), None,
                        number, section, {}, [], "heading",
                    )
                if kind:
                    current_detail = (kind, entity_id)
            continue
        if current_detail and stripped.startswith(("- ", "* ")) and ":" in stripped:
            raw_key, raw_value = stripped[2:].split(":", 1)
            field = clean(raw_key)
            value = clean(raw_value)
            if field and value:
                previous = entities[current_detail]
                fields = {**previous.fields, field: value}
                status = value if field.lower() == "status" else previous.status
                entities[current_detail] = replace(previous, fields=fields, status=status,
                                                   refs=references(" ".join(fields.values()), previous.entity_id))
        meta = _metadata(line)
        if meta:
            metadata[meta[0]] = meta[1]
        cells = split_table(line)
        if not cells:
            headers = None
            continue
        if headers is None:
            headers = cells
            continue
        if _separator(cells):
            continue
        if len(cells) != len(headers):
            warnings.append(f"line {number}: table width {len(cells)} != header {len(headers)}")
            continue
        if section.lower().startswith("pass counter") and len(cells) >= 2:
            try:
                counters[cells[0]] = int(cells[1].replace(",", ""))
            except ValueError:
                pass
        if "fingerprint" in section.lower() or section.lower() == "artifact fingerprints":
            if len(cells) >= 2:
                fingerprints[cells[0]] = cells[1]
        candidate = _id_from_cell(cells[0])
        if not candidate:
            continue
        kind, entity_id = candidate
        fields = dict(zip(headers, cells, strict=True))
        status = next((v for k, v in fields.items() if k.lower() == "status"), None)
        title = next((fields[key] for key in TITLE_COLUMNS.get(kind, ()) if fields.get(key)), entity_id)
        refs = references(" ".join(cells[1:]), entity_id)
        current = entities.get((kind, entity_id))
        entry = ParsedEntity(kind, entity_id, title, status, number, section, fields, refs, "table")
        # Registry tables are authoritative; detail-section headings fill gaps only.
        if current is None or current.source == "heading" or (current.status is None and status):
            entities[(kind, entity_id)] = entry
    return ParsedArtifact(list(entities.values()), metadata, counters, fingerprints, warnings)
