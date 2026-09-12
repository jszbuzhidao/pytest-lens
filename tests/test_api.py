"""HTTP 接口层：看板依赖的每一个 JSON 端点。"""

import pytest
from fastapi.testclient import TestClient

from lens.api import DEFAULT_DB, create_app, load_index_html


@pytest.fixture
def client(populated_db):
    with TestClient(create_app(populated_db)) as c:
        yield c


@pytest.fixture
def empty_client(tmp_path):
    with TestClient(create_app(tmp_path / "nothing.db")) as c:
        yield c


# ------------------------------------------------------------------ 静态与健康


def test_index_serves_the_dashboard_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "pytest-lens" in resp.text


def test_shipped_index_html_is_self_contained():
    html = load_index_html()
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "/api/summary" in html
    assert "<script" in html


def test_healthz_reports_the_database_in_use(client, populated_db):
    data = client.get("/healthz").json()
    assert data["ok"] is True
    assert data["db"] == populated_db


def test_default_db_constant_points_at_the_local_cache():
    assert DEFAULT_DB == ".lens/lens.db"


def test_openapi_and_docs_are_exposed(client):
    assert client.get("/api/openapi.json").status_code == 200
    assert client.get("/api/docs").status_code == 200


# ----------------------------------------------------------------------- projects


def test_projects_lists_recorded_projects(client):
    data = client.get("/api/projects").json()
    assert data["projects"][0]["project"] == "svc"
    assert data["projects"][0]["runs"] == 3


# ----------------------------------------------------------------------- summary


def test_summary_defaults_to_the_newest_project(client):
    data = client.get("/api/summary").json()
    assert data["project"] == "svc"
    assert data["latest"]["id"] == 3
    assert data["flaky"] == 2


def test_summary_accepts_an_explicit_project(client):
    assert client.get("/api/summary", params={"project": "svc"}).json()["project"] == "svc"


def test_summary_for_an_unknown_project_is_empty_not_an_error(client):
    data = client.get("/api/summary", params={"project": "ghost"}).json()
    assert data["latest"] is None
    assert data["delta"] == {}


def test_summary_without_any_project_in_the_db_is_404(empty_client):
    resp = empty_client.get("/api/summary")
    assert resp.status_code == 404
    assert "没有任何运行记录" in resp.json()["detail"]


# -------------------------------------------------------------------------- runs


def test_runs_returns_history_newest_first(client):
    data = client.get("/api/runs").json()
    assert [r["id"] for r in data["runs"]] == [3, 2, 1]
    assert data["project"] == "svc"


def test_runs_respects_the_limit(client):
    assert len(client.get("/api/runs", params={"limit": 1}).json()["runs"]) == 1


def test_runs_rejects_out_of_range_limit(client):
    assert client.get("/api/runs", params={"limit": 0}).status_code == 422
    assert client.get("/api/runs", params={"limit": 9999}).status_code == 422


def test_run_detail_returns_run_and_cases(client):
    data = client.get("/api/runs/3").json()
    assert data["run"]["id"] == 3
    assert data["run"]["skipped"] == 1
    assert {c["nodeid"] for c in data["cases"]} == {
        "tests/test_cart.py::test_ok",
        "tests/test_cart.py::test_total",
        "tests/test_checkout.py::test_wobble",
        "tests/test_cart.py::test_never",
    }


def test_run_detail_for_unknown_id_is_404(client):
    resp = client.get("/api/runs/999")
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]


# ------------------------------------------------------------------------- trend


def test_trend_is_chronological(client):
    data = client.get("/api/trend").json()
    assert [r["id"] for r in data["trend"]] == [1, 2, 3]


def test_trend_accepts_a_limit(client):
    assert len(client.get("/api/trend", params={"limit": 2}).json()["trend"]) == 2


# ----------------------------------------------------------------------- slowest


def test_slowest_returns_the_slowest_first_and_hides_skipped(client):
    """slowest 默认只看最近一次运行；skipped 不算，因为它根本没跑。"""
    data = client.get("/api/slowest").json()
    assert [c["nodeid"] for c in data["slowest"]] == [
        "tests/test_cart.py::test_total",
        "tests/test_checkout.py::test_wobble",
        "tests/test_cart.py::test_ok",
    ]


# ---------------------------------------------------------------------- clusters


def test_clusters_collapse_the_same_bug(client):
    clusters = client.get("/api/clusters").json()["clusters"]
    cart = next(c for c in clusters if c["sample_cases"] == ["tests/test_cart.py::test_total"])
    assert cart["occurrences"] == 2, "两次运行里的同一处断言失败，应该归到一个指纹下"
    assert cart["cases"] == 1


def test_clusters_keep_distinct_bugs_apart(client):
    clusters = client.get("/api/clusters").json()["clusters"]
    assert len(clusters) == 2
    assert len({c["fingerprint"] for c in clusters}) == 2


# ------------------------------------------------------------------------- flaky


def test_flaky_lists_both_unstable_cases(client):
    flaky = client.get("/api/flaky", params={"min_runs": 3}).json()["flaky"]
    assert {f["nodeid"] for f in flaky} == {
        "tests/test_cart.py::test_total",
        "tests/test_checkout.py::test_wobble",
    }


def test_flaky_is_empty_when_min_runs_is_impossible(client):
    assert client.get("/api/flaky", params={"min_runs": 50}).json()["flaky"] == []


def test_flaky_rejects_min_runs_below_two(client):
    assert client.get("/api/flaky", params={"min_runs": 1}).status_code == 422


# ------------------------------------------------------------------ case history


def test_case_history_returns_every_appearance(client):
    data = client.get("/api/cases/history", params={"nodeid": "tests/test_cart.py::test_total"}).json()
    assert [h["outcome"] for h in data["history"]] == ["failed", "passed", "failed"]


def test_case_history_requires_a_nodeid(client):
    assert client.get("/api/cases/history").status_code == 422


# -------------------------------------------------------------------- report.md


def test_report_endpoint_serves_markdown(client):
    resp = client.get("/api/report.md")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert resp.text.startswith("# 测试质量报告 · svc")


# ---------------------------------------------------------------- default project


def test_default_project_short_circuits_resolution(populated_db):
    with TestClient(create_app(populated_db, default_project="svc")) as c:
        assert c.get("/api/runs").json()["project"] == "svc"


def test_default_project_used_when_db_is_empty(tmp_path):
    with TestClient(create_app(tmp_path / "nothing.db", default_project="svc")) as c:
        assert c.get("/api/summary").json()["project"] == "svc"
