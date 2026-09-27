from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import sdlc_lens.collector as collector_module
from sdlc_lens.api import create_app
from sdlc_lens.collector import collect
from sdlc_lens.discovery import discover
from sdlc_lens.parser import parse, references
from sdlc_lens.store import Store


def fixture_repo(root: Path) -> Path:
    feature = root / "docs" / "features" / "sample"
    feature.mkdir(parents=True)
    (feature / "spec.md").write_text("# Sample feature\n", encoding="utf-8")
    (feature / "spec.micro-guarantees.md").write_text(
        "# Micro-guarantees\n- **Source spec:** `docs/features/sample/spec.md`\n"
        "## Backlog\n| ID | Guarantee | Dependencies | Status |\n| --- | --- | --- | --- |\n"
        "| MG-001 | First truth | None | READY |\n"
        "| MG-002 | Second truth | MG-001 | READY |\n", encoding="utf-8",
    )
    (feature / "spec.law-suites.md").write_text(
        "# Laws\n## Law suite index\n| LS ID | Law | Covered MG | Status |\n"
        "| --- | --- | --- | --- |\n| LS-001 | Shared law | MG-001–MG-002 | SEALED |\n",
        encoding="utf-8",
    )
    (feature / "spec.law-test-matrix.md").write_text(
        "# Matrix\n## Cell registry\n"
        "| LC ID | LS ID | Path / state | Evidence source | Status |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| LC-001 | LS-001 | first path | test at `abc1234` | PARTIAL |\n",
        encoding="utf-8",
    )
    (feature / "spec.development-cycle.state.md").write_text(
        "# Controller\n## Header\n- `Source spec:` `spec.md`\n"
        "- `Cycle status:` CONVERGED\n- `Current forward generation:` 2\n"
        "## Pass counters\n| Counter | Value |\n| --- | ---: |\n"
        "| reverse executed passes | 3 |\n", encoding="utf-8",
    )
    return root


def test_parser_extracts_owner_rows_and_expands_ranges(tmp_path: Path):
    repo = fixture_repo(tmp_path)
    artifacts, warnings = discover(repo)
    assert warnings == []
    by_kind = {a.kind: a for a in artifacts}
    guarantees = parse(by_kind["guarantees"])
    assert [(x.entity_id, x.status) for x in guarantees.entities] == [
        ("MG-001", "READY"), ("MG-002", "READY")]
    laws = parse(by_kind["laws"])
    assert laws.entities[0].refs == [
        {"kind": "MG", "id": "MG-001", "confidence": "text_reference"},
        {"kind": "MG", "id": "MG-002", "confidence": "text_reference"},
    ]
    controller = parse(by_kind["controller"])
    assert controller.metadata["cycle status"] == "CONVERGED"
    assert controller.counters["reverse executed passes"] == 3
    assert references("LC-001–LC-003")[-1]["id"] == "LC-003"


def test_project_guard_heading_and_fields(tmp_path: Path):
    repo = fixture_repo(tmp_path)
    guards = repo / "docs/development/process-guards.md"
    guards.parent.mkdir(parents=True)
    guards.write_text(
        "# Project guards\n## PG-PROJ-001 — Preserve owners\n"
        "- Status: `ACTIVE`\n- Origin: NF-003\n- Challenge: trace owners.\n",
        encoding="utf-8",
    )
    artifacts, _ = discover(repo)
    guard = parse(next(a for a in artifacts if a.kind == "project_guards")).entities[0]
    assert (guard.kind, guard.entity_id, guard.status) == ("PG", "PG-PROJ-001", "ACTIVE")
    assert guard.fields["Origin"] == "NF-003"


def test_scan_is_idempotent_and_preserves_history_without_writing_source(tmp_path: Path):
    repo = fixture_repo(tmp_path / "touristico")
    db = tmp_path / "lens.db"
    store = Store("sqlite:///" + str(db))
    source = repo / "docs/features/sample/spec.law-test-matrix.md"
    original = source.read_bytes()
    first = collect(repo, store)
    assert first["changed"] is True
    assert first["change_count"] > 0
    second = collect(repo, store)
    assert second["changed"] is False
    assert second["scan_id"] == first["scan_id"]
    assert source.read_bytes() == original
    source.write_text(source.read_text().replace("PARTIAL", "BOUND_GREEN"), encoding="utf-8")
    third = collect(repo, store)
    assert third["changed"] is True
    changes = store.changes_for("touristico", "docs/features/sample/spec.md")
    change = next(c for c in changes if c["entity_id"] == "LC-001" and c["change_type"] == "updated")
    assert change["before"]["status"] == "PARTIAL"
    assert change["after"]["status"] == "BOUND_GREEN"
    assert len(store.scan_history("touristico")) == 2
    current = store.current("touristico")
    old_state = store.current("touristico", first["scan_id"])
    old_lc = next(e for e in old_state["entities"] if e["entity_id"] == "LC-001")
    new_lc = next(e for e in current["entities"] if e["entity_id"] == "LC-001")
    assert old_lc["status"] == "PARTIAL"
    assert new_lc["status"] == "BOUND_GREEN"
    assert old_state["scan"]["last_checked_at"] == old_state["scan"]["observed_at"]
    client = TestClient(create_app(store))
    old_response = client.get("/api/entity", params={
        "key": "docs/features/sample/spec.md", "kind": "LC", "entity_id": "LC-001",
        "scan_id": first["scan_id"],
    })
    assert old_response.status_code == 200
    assert old_response.json()["status"] == "PARTIAL"
    assert client.get("/api/features", params={"scan_id": "missing"}).status_code == 404
    controller = next(a for a in current["artifacts"] if a["kind"] == "controller")
    assert controller["metadata"]["cycle status"] == "CONVERGED"
    store.close()


def test_api_keeps_declared_status_separate_from_verified_readiness(tmp_path: Path):
    repo = fixture_repo(tmp_path / "touristico")
    controller = repo / "docs/features/sample/spec.development-cycle.state.md"
    controller.write_text(controller.read_text() +
                          "\n## Artifact fingerprints\n| Artifact | SHA-256 |\n| --- | --- |\n"
                          "| Source spec | " + "0" * 64 + " |\n", encoding="utf-8")
    store = Store("sqlite:///" + str(tmp_path / "lens.db"))
    collect(repo, store)
    client = TestClient(create_app(store))
    items = client.get("/api/features").json()["items"]
    assert len(items) == 1
    assert items[0]["declared_status"] == "CONVERGED"
    assert items[0]["readiness"] == "not_independently_verified"
    assert "usage_unknown" in items[0]["quality"]
    assert "artifact_fingerprint_mismatch" in items[0]["quality"]
    entity = client.get("/api/entity", params={
        "key": "docs/features/sample/spec.md", "kind": "LC", "entity_id": "LC-001"
    }).json()
    assert entity["status"] == "PARTIAL"
    assert entity["path"] == "docs/features/sample/spec.law-test-matrix.md"
    assert client.get("/feature", params={"key": "docs/features/sample/spec.md"}).status_code == 200
    assert client.get(f"/artifact/{entity['artifact_id']}").status_code == 200
    assert 'id="L5"' in client.get(f"/artifact/{entity['artifact_id']}").text
    assert client.get("/api/search", params={"q": "LC-001"}).json()["total"] == 1
    assert client.get("/api/data-quality").json()["llm_usage"] == "unknown"
    assert client.get("/api/entity", params={
        "key": "docs/features/sample/spec.md", "kind": "LC", "entity_id": "LC-999"
    }).status_code == 404
    store.close()


def test_untracked_worktree_snapshot_gets_own_version_after_git_commit(tmp_path: Path):
    repo = fixture_repo(tmp_path / "touristico")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "docs"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-qm", "initial"], check=True)
    matrix = repo / "docs/features/sample/spec.law-test-matrix.md"
    matrix.write_text(matrix.read_text().replace("PARTIAL", "BOUND_GREEN"), encoding="utf-8")
    store = Store("sqlite:///" + str(tmp_path / "lens.db"))
    collect(repo, store)
    dirty = next(a for a in store.current("touristico")["artifacts"] if a["kind"] == "matrix")
    assert dirty["dirty"] is True
    subprocess.run(["git", "-C", str(repo), "add", "docs"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-qm", "matrix"], check=True)
    collect(repo, store)
    clean = next(a for a in store.current("touristico")["artifacts"] if a["kind"] == "matrix")
    assert clean["dirty"] is False
    assert clean["content_hash"] == dirty["content_hash"]
    assert clean["id"] != dirty["id"]
    store.close()


def test_head_change_during_scan_does_not_publish(tmp_path: Path, monkeypatch):
    repo = fixture_repo(tmp_path / "touristico")
    store = Store("sqlite:///" + str(tmp_path / "lens.db"))
    heads = iter(["a" * 40, "b" * 40])
    monkeypatch.setattr(collector_module, "git_head", lambda _: next(heads))
    with pytest.raises(RuntimeError, match="HEAD changed"):
        collect(repo, store)
    assert store.current("touristico")["scan"] is None
    store.close()
