"""Transactional snapshot store. SQLite locally; PostgreSQL with psycopg in deployment."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean, Column, ForeignKey, Index, Integer, MetaData, String, Table,
    Text, create_engine, select, text,
)
from sqlalchemy.engine import Engine

from .discovery import Artifact
from .parser import OWNER_KIND, PARSER_VERSION, ParsedArtifact


metadata = MetaData()
scans = Table(
    "scans", metadata,
    Column("id", String(36), primary_key=True),
    Column("repository", Text, nullable=False),
    Column("head", String(64)),
    Column("manifest_hash", String(64), nullable=False),
    Column("observed_at", Text, nullable=False),
    Column("file_count", Integer, nullable=False),
    Column("warnings_json", Text, nullable=False),
)
Index("ix_scans_repository_time", scans.c.repository, scans.c.observed_at)
artifacts = Table(
    "artifacts", metadata,
    Column("id", String(64), primary_key=True),
    Column("repository", Text, nullable=False),
    Column("path", Text, nullable=False),
    Column("kind", String(40), nullable=False),
    Column("feature_key", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("git_blob", String(64)),
    Column("dirty", Boolean, nullable=False),
    Column("content", Text, nullable=False),
    Column("parser_version", String(40), nullable=False),
    Column("metadata_json", Text, nullable=False),
    Column("counters_json", Text, nullable=False),
    Column("fingerprints_json", Text, nullable=False),
    Column("warnings_json", Text, nullable=False),
)
Index("ix_artifacts_repository_path", artifacts.c.repository, artifacts.c.path)
scan_artifacts = Table(
    "scan_artifacts", metadata,
    Column("scan_id", String(36), ForeignKey("scans.id"), primary_key=True),
    Column("artifact_id", String(64), ForeignKey("artifacts.id"), primary_key=True),
)
entities = Table(
    "entities", metadata,
    Column("id", String(64), primary_key=True),
    Column("artifact_id", String(64), ForeignKey("artifacts.id"), nullable=False),
    Column("feature_key", Text, nullable=False),
    Column("kind", String(8), nullable=False),
    Column("entity_id", String(30), nullable=False),
    Column("title", Text, nullable=False),
    Column("status", Text),
    Column("line", Integer, nullable=False),
    Column("section", Text, nullable=False),
    Column("fields_json", Text, nullable=False),
    Column("refs_json", Text, nullable=False),
    Column("source", String(20), nullable=False),
)
Index("ix_entities_artifact", entities.c.artifact_id)
Index("ix_entities_feature_kind", entities.c.feature_key, entities.c.kind, entities.c.entity_id)
changes = Table(
    "changes", metadata,
    Column("id", String(64), primary_key=True),
    Column("scan_id", String(36), ForeignKey("scans.id"), nullable=False),
    Column("repository", Text, nullable=False),
    Column("feature_key", Text, nullable=False),
    Column("kind", String(8), nullable=False),
    Column("entity_id", String(30), nullable=False),
    Column("change_type", String(20), nullable=False),
    Column("before_json", Text),
    Column("after_json", Text),
)
Index("ix_changes_scan", changes.c.scan_id)
repo_state = Table(
    "repo_state", metadata,
    Column("repository", Text, primary_key=True),
    Column("last_scan_id", String(36)),
    Column("last_checked_at", Text, nullable=False),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def key_hash(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


class Store:
    def __init__(self, database_url: str):
        self.engine: Engine = create_engine(database_url, future=True)
        metadata.create_all(self.engine)

    def close(self) -> None:
        self.engine.dispose()

    def _latest_scan_id(self, conn, repository: str) -> str | None:
        return conn.execute(
            select(repo_state.c.last_scan_id).where(repo_state.c.repository == repository)
        ).scalar_one_or_none()

    def latest_scan_id(self, repository: str) -> str | None:
        with self.engine.connect() as conn:
            return self._latest_scan_id(conn, repository)

    def _scan_entities(self, conn, scan_id: str | None) -> dict[tuple[str, str, str], dict[str, Any]]:
        if not scan_id:
            return {}
        rows = conn.execute(
            select(entities, artifacts.c.kind.label("artifact_kind"), artifacts.c.path,
                   artifacts.c.content_hash, artifacts.c.dirty, artifacts.c.git_blob)
            .join(artifacts, entities.c.artifact_id == artifacts.c.id)
            .join(scan_artifacts, scan_artifacts.c.artifact_id == artifacts.c.id)
            .where(scan_artifacts.c.scan_id == scan_id)
        ).mappings().all()
        result: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            item["fields"] = json.loads(item.pop("fields_json"))
            item["refs"] = json.loads(item.pop("refs_json"))
            key = (item["feature_key"], item["kind"], item["entity_id"])
            old = result.get(key)
            owner = OWNER_KIND.get(item["kind"])
            if old is None or (item["artifact_kind"] == owner and old["artifact_kind"] != owner):
                result[key] = item
        return result

    def collect(self, repository: str, head: str | None,
                found: list[Artifact], parsed: dict[str, ParsedArtifact],
                discovery_warnings: list[str]) -> dict[str, Any]:
        manifest = key_hash(PARSER_VERSION, head or "", *(f"{a.path}:{a.sha256}:{a.git_blob}:{a.dirty}" for a in found))
        now = utc_now()
        with self.engine.begin() as conn:
            previous_id = self._latest_scan_id(conn, repository)
            previous = conn.execute(select(scans).where(scans.c.id == previous_id)).mappings().first() if previous_id else None
            if previous and previous["manifest_hash"] == manifest:
                conn.execute(
                    repo_state.update().where(repo_state.c.repository == repository)
                    .values(last_checked_at=now)
                )
                return {"scan_id": previous_id, "changed": False, "file_count": len(found),
                        "change_count": 0, "warnings": json.loads(previous["warnings_json"]),
                        "checked_at": now}
            scan_id = str(uuid.uuid4())
            warnings = list(discovery_warnings)
            for artifact in found:
                warnings.extend(f"{artifact.path}: {w}" for w in parsed[artifact.path].warnings)
            conn.execute(scans.insert().values(
                id=scan_id, repository=repository, head=head, manifest_hash=manifest,
                observed_at=now, file_count=len(found), warnings_json=json_dumps(warnings),
            ))
            existing_ids = set(conn.execute(select(artifacts.c.id).where(
                artifacts.c.repository == repository
            )).scalars())
            for artifact in found:
                artifact_id = key_hash(PARSER_VERSION, repository, artifact.path, artifact.sha256,
                                       artifact.git_blob or "", str(artifact.dirty))
                parsed_artifact = parsed[artifact.path]
                if artifact_id not in existing_ids:
                    conn.execute(artifacts.insert().values(
                        id=artifact_id, repository=repository, path=artifact.path,
                        kind=artifact.kind, feature_key=artifact.feature_key,
                        content_hash=artifact.sha256, git_blob=artifact.git_blob,
                        dirty=artifact.dirty, content=artifact.content,
                        parser_version=PARSER_VERSION,
                        metadata_json=json_dumps(parsed_artifact.metadata),
                        counters_json=json_dumps(parsed_artifact.counters),
                        fingerprints_json=json_dumps(parsed_artifact.fingerprints),
                        warnings_json=json_dumps(parsed_artifact.warnings),
                    ))
                    for entity in parsed_artifact.entities:
                        conn.execute(entities.insert().values(
                            id=key_hash(artifact_id, entity.kind, entity.entity_id),
                            artifact_id=artifact_id, feature_key=artifact.feature_key,
                            kind=entity.kind, entity_id=entity.entity_id,
                            title=entity.title, status=entity.status, line=entity.line,
                            section=entity.section, fields_json=json_dumps(entity.fields),
                            refs_json=json_dumps(entity.refs), source=entity.source,
                        ))
                    existing_ids.add(artifact_id)
                conn.execute(scan_artifacts.insert().values(scan_id=scan_id, artifact_id=artifact_id))
            before = self._scan_entities(conn, previous_id)
            after = self._scan_entities(conn, scan_id)
            previous_parser_versions = set()
            if previous_id:
                previous_parser_versions = set(conn.execute(
                    select(artifacts.c.parser_version)
                    .join(scan_artifacts, scan_artifacts.c.artifact_id == artifacts.c.id)
                    .where(scan_artifacts.c.scan_id == previous_id)
                ).scalars())
            reindexed = bool(previous_parser_versions and previous_parser_versions != {PARSER_VERSION})
            count = 0
            for key in sorted(before.keys() | after.keys()):
                old, new = before.get(key), after.get(key)
                old_value = {"title": old["title"], "status": old["status"], "fields": old["fields"]} if old else None
                new_value = {"title": new["title"], "status": new["status"], "fields": new["fields"]} if new else None
                if old_value == new_value:
                    continue
                change_type = ("baseline_import" if previous_id is None else
                               "parser_reindex" if reindexed else
                               "added" if old is None else "removed" if new is None else "updated")
                conn.execute(changes.insert().values(
                    id=key_hash(scan_id, *key), scan_id=scan_id, repository=repository,
                    feature_key=key[0], kind=key[1], entity_id=key[2],
                    change_type=change_type,
                    before_json=json_dumps(old_value) if old_value else None,
                    after_json=json_dumps(new_value) if new_value else None,
                ))
                count += 1
            if previous_id:
                conn.execute(repo_state.update().where(repo_state.c.repository == repository).values(
                    last_scan_id=scan_id, last_checked_at=now,
                ))
            else:
                conn.execute(repo_state.insert().values(
                    repository=repository, last_scan_id=scan_id, last_checked_at=now,
                ))
        return {"scan_id": scan_id, "changed": True, "file_count": len(found),
                "change_count": count, "warnings": warnings, "checked_at": now}

    def current(self, repository: str, scan_id: str | None = None) -> dict[str, Any]:
        with self.engine.connect() as conn:
            latest_id = self._latest_scan_id(conn, repository)
            scan_id = scan_id or latest_id
            if not scan_id:
                return {"scan": None, "artifacts": [], "entities": []}
            scan = conn.execute(select(scans).where(scans.c.id == scan_id,
                                                    scans.c.repository == repository)).mappings().first()
            if scan is None:
                raise KeyError(scan_id)
            checked = (conn.execute(select(repo_state.c.last_checked_at).where(
                repo_state.c.repository == repository
            )).scalar_one() if scan_id == latest_id else scan["observed_at"])
            artifact_rows = conn.execute(
                select(artifacts).join(scan_artifacts, scan_artifacts.c.artifact_id == artifacts.c.id)
                .where(scan_artifacts.c.scan_id == scan_id)
            ).mappings().all()
            artifact_items = []
            for row in artifact_rows:
                item = {k: row[k] for k in ("id", "path", "kind", "feature_key", "content_hash", "git_blob", "dirty", "parser_version")}
                for key in ("metadata", "counters", "fingerprints", "warnings"):
                    item[key] = json.loads(row[f"{key}_json"])
                artifact_items.append(item)
            entity_items = list(self._scan_entities(conn, scan_id).values())
            for item in entity_items:
                item.pop("fields_json", None)
                item.pop("refs_json", None)
            return {"scan": {**dict(scan), "warnings": json.loads(scan["warnings_json"]),
                             "last_checked_at": checked},
                    "artifacts": artifact_items, "entities": entity_items}

    def artifact_content(self, artifact_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(artifacts).where(artifacts.c.id == artifact_id)).mappings().first()
            return dict(row) if row else None

    def changes_for(self, repository: str, feature_key: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            query = (select(changes, scans.c.observed_at)
                     .join(scans, changes.c.scan_id == scans.c.id)
                     .where(changes.c.repository == repository)
                     .order_by(scans.c.observed_at.desc(), changes.c.entity_id)
                     .limit(limit))
            if feature_key:
                query = query.where(changes.c.feature_key == feature_key)
            rows = conn.execute(query).mappings().all()
            return [{**dict(row),
                     "before": json.loads(row["before_json"]) if row["before_json"] else None,
                     "after": json.loads(row["after_json"]) if row["after_json"] else None}
                    for row in rows]

    def scan_history(self, repository: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(scans).where(scans.c.repository == repository)
                                .order_by(scans.c.observed_at.desc()).limit(limit)).mappings().all()
            return [{**dict(row), "warnings": json.loads(row["warnings_json"])} for row in rows]
