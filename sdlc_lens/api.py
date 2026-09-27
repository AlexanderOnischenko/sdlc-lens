"""Read-only HTTP API and small server-rendered console."""

from __future__ import annotations

import html
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from .store import Store
from .views import feature, features


STYLE = """
body{font:15px/1.5 system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#1b2633;background:#f8fafc}
a{color:#145b9a}table{border-collapse:collapse;width:100%;background:white}th,td{text-align:left;border-bottom:1px solid #dce3ea;padding:.55rem;vertical-align:top}
th{background:#eaf0f5}code{font-size:.9em;background:#eef2f6;padding:.1rem .25rem}.muted{color:#657180}.pill{display:inline-block;border-radius:999px;padding:.15rem .5rem;background:#e3eaf3;margin:.1rem}
.warning{background:#fff0d6;padding:.7rem;border-left:4px solid #c57900}.card{background:white;border:1px solid #dce3ea;border-radius:8px;padding:1rem;margin:1rem 0}
details{margin:.5rem 0}pre{white-space:pre-wrap;word-break:break-word}
pre div:target{background:#fff0bd}
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f'<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>{STYLE}</style><body><nav><a href="/">SDLC Lens</a> · <a href="/api/features">JSON API</a></nav>{body}</body></html>')


def _tag(value: str | None) -> str:
    return f'<span class="pill">{html.escape(value or "unknown")}</span>'


def create_app(store: Store, repository: str = "touristico") -> FastAPI:
    app = FastAPI(title="SDLC Lens", version="0.1.0")
    app.state.store = store
    app.state.repository = repository

    def state(scan_id: str | None = None) -> dict:
        try:
            return store.current(repository, scan_id)
        except KeyError:
            raise HTTPException(404, "Scan not found") from None

    @app.get("/api/health")
    def health():
        current = state()
        return {"ok": True, "repository": repository,
                "scan_id": current["scan"]["id"] if current["scan"] else None,
                "last_checked_at": current["scan"]["last_checked_at"] if current["scan"] else None}

    @app.get("/api/features")
    def list_features(scan_id: str | None = None):
        current = state(scan_id)
        return {"as_of": current["scan"]["observed_at"] if current["scan"] else None,
                "items": features(current)}

    @app.get("/api/feature")
    def get_feature(key: str = Query(...), scan_id: str | None = None):
        item = feature(state(scan_id), key)
        if item is None:
            raise HTTPException(404, "Feature not found")
        return item

    @app.get("/api/entities")
    def list_entities(key: str | None = None, kind: str | None = None,
                      status: str | None = None, limit: int = Query(200, ge=1, le=1000),
                      offset: int = Query(0, ge=0), scan_id: str | None = None):
        items = [e for e in state(scan_id)["entities"] if
                 (key is None or e["feature_key"] == key) and
                 (kind is None or e["kind"] == kind) and
                 (status is None or e["status"] == status)]
        items.sort(key=lambda e: (e["feature_key"], e["kind"], e["entity_id"]))
        return {"total": len(items), "offset": offset, "items": items[offset:offset + limit]}

    @app.get("/api/entity")
    def get_entity(key: str, kind: str, entity_id: str, scan_id: str | None = None):
        item = next((e for e in state(scan_id)["entities"] if e["feature_key"] == key
                     and e["kind"] == kind and e["entity_id"] == entity_id), None)
        if item is None:
            raise HTTPException(404, "Entity not found")
        return item

    @app.get("/api/changes")
    def list_changes(key: str | None = None, limit: int = Query(100, ge=1, le=1000)):
        return {"items": store.changes_for(repository, key, limit)}

    @app.get("/api/scans")
    def list_scans(limit: int = Query(50, ge=1, le=500)):
        return {"items": store.scan_history(repository, limit)}

    @app.get("/api/search")
    def search(q: str = Query(..., min_length=2), limit: int = Query(100, ge=1, le=500)):
        needle = q.casefold()
        matches = [e for e in state()["entities"] if needle in e["entity_id"].casefold()
                   or needle in e["title"].casefold()]
        matches.sort(key=lambda e: (e["entity_id"].casefold() != needle,
                                    e["feature_key"], e["kind"], e["entity_id"]))
        return {"total": len(matches), "items": matches[:limit]}

    @app.get("/api/data-quality")
    def data_quality():
        current = state()
        return {
            "as_of": current["scan"]["observed_at"] if current["scan"] else None,
            "warnings": current["scan"]["warnings"] if current["scan"] else ["no_scan"],
            "uncommitted_artifacts": [a["path"] for a in current["artifacts"] if a["dirty"]],
            "pass_history": "partial_from_artifacts",
            "llm_usage": "unknown",
            "cost": "unknown",
        }

    @app.get("/api/artifacts/{artifact_id}")
    def get_artifact(artifact_id: str):
        artifact = store.artifact_content(artifact_id)
        if artifact is None or artifact["repository"] != repository:
            raise HTTPException(404, "Artifact version not found")
        return {"id": artifact_id, "path": artifact["path"],
                "hash": artifact["content_hash"], "content": artifact["content"],
                "dirty": artifact["dirty"], "git_blob": artifact["git_blob"]}

    @app.get("/artifact/{artifact_id}", response_class=HTMLResponse)
    def artifact_page(artifact_id: str):
        artifact = store.artifact_content(artifact_id)
        if artifact is None or artifact["repository"] != repository:
            raise HTTPException(404, "Artifact version not found")
        lines = "".join(
            f'<div id="L{number}"><a class="muted" href="#L{number}">{number:4}</a> {html.escape(line)}</div>'
            for number, line in enumerate(artifact["content"].splitlines(), 1)
        )
        return _page(artifact["path"],
                     f'<h1>{html.escape(artifact["path"])}</h1><p>SHA-256 <code>{artifact["content_hash"]}</code> · {"worktree" if artifact["dirty"] else "Git HEAD"}</p><pre>{lines}</pre>')

    @app.get("/", response_class=HTMLResponse)
    def home():
        current = state()
        scan = current["scan"]
        if not scan:
            return _page("SDLC Lens", '<h1>SDLC Lens</h1><p class="warning">Нет снимка. Запустите <code>sdlc-lens scan</code>.</p>')
        rows = []
        for item in features(current):
            href = "/feature?key=" + quote(item["key"], safe="")
            rows.append(f'<tr><td><a href="{href}">{html.escape(item["title"])}</a><br><small class="muted">{html.escape(item["key"])}</small></td><td>{_tag(item["declared_status"])}</td><td>{html.escape(str(item["generation"] or "—"))}</td><td>{item["counts"].get("MG",0)}</td><td>{item["counts"].get("LC",0)}</td><td>{item["counts"].get("BG",0)}</td><td>{html.escape(", ".join(item["quality"]))}</td></tr>')
        warning = f'<p class="warning">Предупреждения разбора: {len(scan["warnings"])}. Состояние показывается по файлам; точные pass/usage неизвестны.</p>'
        body = f'<h1>Фичи</h1><p class="muted">Снимок {html.escape(scan["observed_at"])} · Git {html.escape((scan["head"] or "unknown")[:12])} · файлов {scan["file_count"]}</p>{warning}<table><tr><th>Фича</th><th>Статус контроллера</th><th>Поколение</th><th>MG</th><th>LC</th><th>BG</th><th>Полнота</th></tr>{"".join(rows)}</table>'
        return _page("SDLC Lens — Фичи", body)

    @app.get("/feature", response_class=HTMLResponse)
    def feature_page(key: str):
        item = feature(state(), key)
        if item is None:
            raise HTTPException(404, "Feature not found")
        counts = " ".join(_tag(f"{kind}: {count}") for kind, count in sorted(item["counts"].items()))
        sections = []
        for kind in ("MG", "LS", "LP", "LC", "BG", "NF", "RV", "PG", "FI", "WI", "RF", "IV"):
            subset = [e for e in item["entities"] if e["kind"] == kind]
            if not subset:
                continue
            rows = []
            for entity in subset:
                link = f'/artifact/{entity["artifact_id"]}'
                rows.append(f'<tr><td><a href="{link}#L{entity["line"]}">{html.escape(entity["entity_id"])}</a></td><td>{html.escape(entity["title"][:240])}</td><td>{_tag(entity["status"])}</td><td><small>{html.escape(entity["path"]) }:{entity["line"]}</small></td></tr>')
            sections.append(f'<details><summary><strong>{kind}</strong> — {len(subset)}</summary><table><tr><th>ID</th><th>Описание</th><th>Статус источника</th><th>Источник</th></tr>{"".join(rows)}</table></details>')
        artifacts_html = "".join(f'<li><a href="/artifact/{a["id"]}">{html.escape(a["path"])}</a> · {a["content_hash"][:12]} {"(worktree)" if a["dirty"] else "(Git)"}</li>' for a in item["artifacts"])
        changes_html = "".join(f'<li>{html.escape(c["observed_at"])}: {html.escape(c["kind"]+"-"+c["entity_id"] if not c["entity_id"].startswith(c["kind"]+"-") else c["entity_id"])} — {html.escape(c["change_type"])}</li>' for c in store.changes_for(repository, key, 30))
        controller = next((a for a in item["artifacts"] if a["kind"] == "controller"), None)
        counters = "" if not controller else "<h2>Счётчики контроллера</h2><table><tr><th>Счётчик</th><th>Значение источника</th></tr>" + "".join(
            f'<tr><td>{html.escape(name)}</td><td>{value}</td></tr>'
            for name, value in controller["counters"].items()) + "</table>"
        checks = "" if not item["fingerprint_checks"] else "<h2>Fingerprint артефактов</h2><table><tr><th>Артефакт</th><th>Сравнение с controller</th></tr>" + "".join(
            f'<tr><td>{html.escape(check["artifact"])}</td><td>{"совпадает" if check["matches"] else "РАСХОЖДЕНИЕ"}</td></tr>'
            for check in item["fingerprint_checks"]) + "</table>"
        body = f'<h1>{html.escape(item["title"])}</h1><p><code>{html.escape(key)}</code></p><div class="card"><p>Статус контроллера: {_tag(item["declared_status"])} · Поколение: {html.escape(str(item["generation"] or "—"))}</p><p>Готовность: <strong>не подтверждена независимой проверкой baseline</strong>. Проходы и usage: неполные данные.</p><p>Текущая фаза: {html.escape(item["phase"] or "не указана")}</p><p>Последняя безопасная точка: {html.escape(item["resume_point"] or "не указана")}</p>{counts}</div>{checks}{counters}<h2>Сущности</h2>{"".join(sections)}<h2>Файлы и версии</h2><ul>{artifacts_html}</ul><h2>Последние изменения</h2><ul>{changes_html}</ul>'
        return _page(f'SDLC Lens — {item["title"]}', body)

    return app
