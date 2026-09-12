"""FastAPI 应用：把 SQLite 里的结果暴露成 JSON API + 一个单页看板。"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse

from .report import build_report
from .store import Store

DEFAULT_DB = ".lens/lens.db"


def load_index_html() -> str:
    return (resources.files("lens") / "web" / "index.html").read_text(encoding="utf-8")


def create_app(db_path: str | Path = DEFAULT_DB, default_project: str | None = None) -> FastAPI:
    app = FastAPI(title="pytest-lens", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.db_path = str(db_path)
    app.state.default_project = default_project

    def open_store() -> Store:
        return Store(app.state.db_path)

    def resolve_project(store: Store, project: str | None) -> str:
        name = project or app.state.default_project
        if name:
            return name
        projects = store.projects()
        if not projects:
            raise HTTPException(status_code=404, detail="数据库里没有任何运行记录")
        return projects[0]["project"]

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return load_index_html()

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "db": app.state.db_path}

    @app.get("/api/projects")
    def projects() -> dict:
        with open_store() as store:
            return {"projects": store.projects()}

    @app.get("/api/summary")
    def summary(project: str | None = None) -> dict:
        with open_store() as store:
            return store.summary(resolve_project(store, project))

    @app.get("/api/runs")
    def runs(project: str | None = None, limit: int = Query(50, ge=1, le=500)) -> dict:
        with open_store() as store:
            name = resolve_project(store, project)
            return {"project": name, "runs": store.runs(name, limit=limit)}

    @app.get("/api/trend")
    def trend(project: str | None = None, limit: int = Query(30, ge=1, le=200)) -> dict:
        with open_store() as store:
            name = resolve_project(store, project)
            return {"project": name, "trend": store.trend(name, limit=limit)}

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: int) -> dict:
        with open_store() as store:
            try:
                return store.run_detail(run_id)
            except KeyError:
                raise HTTPException(status_code=404, detail=f"run {run_id} 不存在")

    @app.get("/api/slowest")
    def slowest(project: str | None = None, limit: int = Query(10, ge=1, le=100)) -> dict:
        with open_store() as store:
            name = resolve_project(store, project)
            return {"project": name, "slowest": store.slowest(name, limit=limit)}

    @app.get("/api/clusters")
    def clusters(project: str | None = None, limit: int = Query(20, ge=1, le=100)) -> dict:
        with open_store() as store:
            name = resolve_project(store, project)
            return {"project": name, "clusters": store.failure_clusters(name, limit=limit)}

    @app.get("/api/flaky")
    def flaky(
        project: str | None = None,
        min_runs: int = Query(3, ge=2, le=50),
        limit: int = Query(20, ge=1, le=100),
    ) -> dict:
        with open_store() as store:
            name = resolve_project(store, project)
            return {"project": name, "flaky": store.flaky(name, min_runs=min_runs, limit=limit)}

    @app.get("/api/cases/history")
    def case_history(nodeid: str, limit: int = Query(20, ge=1, le=200)) -> dict:
        with open_store() as store:
            return {"nodeid": nodeid, "history": store.case_history(nodeid, limit=limit)}

    @app.get("/api/report.md", response_class=PlainTextResponse)
    def report_md(project: str | None = None) -> str:
        with open_store() as store:
            return build_report(store, resolve_project(store, project))

    return app
