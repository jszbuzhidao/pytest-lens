"""命令行入口：每条子命令都要能跑通，且对错误输入有明确说法。"""

import textwrap

import pytest

from lens.cli import build_parser, main
from lens.store import Store

JUNIT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="pytest" tests="3" failures="1" skipped="1">
    <testcase classname="tests.test_api" name="test_ok" time="0.01"/>
    <testcase classname="tests.test_api" name="test_bad" time="0.02">
      <failure message="AssertionError: expected 200">E       AssertionError: expected 200

tests/test_api.py:12: AssertionError</failure>
    </testcase>
    <testcase classname="tests.test_api" name="test_skip" time="0.0">
      <skipped message="pending"/>
    </testcase>
  </testsuite>
</testsuites>
"""


def write_xml(directory, name, body=JUNIT):
    path = directory / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


# ------------------------------------------------------------------ seed-demo


def test_seed_demo_writes_the_requested_number_of_runs(tmp_path, capsys):
    db = tmp_path / "demo.db"
    assert main(["seed-demo", "--db", str(db), "--runs", "4"]) == 0
    assert "已写入 4 次演示运行" in capsys.readouterr().out
    with Store(db) as store:
        assert len(store.runs("demo-shop", limit=99)) == 4


def test_seed_demo_is_deterministic_for_a_given_seed(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    main(["seed-demo", "--db", str(a), "--runs", "3", "--seed", "11"])
    main(["seed-demo", "--db", str(b), "--runs", "3", "--seed", "11"])
    with Store(a) as sa, Store(b) as sb:
        assert sa.runs("demo-shop")[0]["passed"] == sb.runs("demo-shop")[0]["passed"]
        assert [c["nodeid"] for c in sa.slowest("demo-shop", limit=5)] == [
            c["nodeid"] for c in sb.slowest("demo-shop", limit=5)
        ]


def test_seed_demo_produces_something_worth_looking_at(tmp_path):
    """演示数据的意义就在于看板上有东西可看：得有聚类、有 flaky、有慢用例。"""
    db = tmp_path / "demo.db"
    main(["seed-demo", "--db", str(db), "--runs", "8"])
    with Store(db) as store:
        assert store.failure_clusters("demo-shop")
        assert store.flaky("demo-shop", min_runs=3)
        assert store.slowest("demo-shop", limit=3)


# ---------------------------------------------------------------------- stats


def test_stats_prints_a_summary(populated_db, capsys):
    assert main(["stats", "--db", populated_db]) == 0
    out = capsys.readouterr().out
    assert "项目：svc" in out
    assert "通过率" in out
    assert "失败聚类" in out
    assert "不稳定用例" in out
    assert "最慢用例" in out


def test_stats_accepts_an_explicit_project(populated_db, capsys):
    assert main(["stats", "--db", populated_db, "--project", "svc", "--top", "2"]) == 0
    assert "svc" in capsys.readouterr().out


def test_stats_exits_with_code_2_when_the_database_is_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        main(["stats", "--db", str(tmp_path / "empty.db")])
    assert excinfo.value.code == 2


# --------------------------------------------------------------------- report


def test_report_writes_the_file_it_was_asked_for(populated_db, tmp_path, capsys):
    out = tmp_path / "report.md"
    assert main(["report", "--db", populated_db, "--out", str(out)]) == 0
    assert str(out) in capsys.readouterr().out
    assert out.read_text(encoding="utf-8").startswith("# 测试质量报告 · svc")


def test_report_prints_to_stdout_without_out(populated_db, capsys):
    assert main(["report", "--db", populated_db]) == 0
    assert "# 测试质量报告 · svc" in capsys.readouterr().out


# --------------------------------------------------------------------- import


def test_import_records_a_junit_xml(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    write_xml(tmp_path, "junit.xml")
    db = tmp_path / "import.db"

    assert main(["import", "junit.xml", "--db", str(db), "--project", "svc",
                 "--commit", "deadbee", "--branch", "release"]) == 0
    assert "导入 junit.xml -> run #1" in capsys.readouterr().out

    with Store(db) as store:
        run = store.runs("svc", limit=1)[0]
        assert (run["total"], run["passed"], run["failed"], run["skipped"]) == (3, 1, 1, 1)
        assert run["git_commit"] == "deadbee"
        assert run["git_branch"] == "release"
        assert store.failure_clusters("svc")[0]["message"]


def test_import_falls_back_to_the_file_name_as_project(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    write_xml(tmp_path, "nightly.xml")
    db = tmp_path / "import.db"
    assert main(["import", "nightly.xml", "--db", str(db)]) == 0
    with Store(db) as store:
        assert store.projects()[0]["project"] == "nightly"


def test_import_accepts_a_glob(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    write_xml(tmp_path, "a.xml")
    write_xml(tmp_path, "b.xml")
    db = tmp_path / "import.db"
    assert main(["import", "*.xml", "--db", str(db), "--project", "batch"]) == 0
    with Store(db) as store:
        assert len(store.runs("batch", limit=99)) == 2


def test_import_reports_a_missing_file_and_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "import.db"
    assert main(["import", "nope.xml", "--db", str(db)]) == 1
    captured = capsys.readouterr()
    assert "找不到文件" in captured.err
    assert "没有导入任何文件" in captured.err


# ---------------------------------------------------------------------- serve


def test_serve_hands_the_app_to_uvicorn(monkeypatch, tmp_path):
    import uvicorn

    captured = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(kw, app=app))
    db = tmp_path / "serve.db"

    assert main(["serve", "--db", str(db), "--host", "0.0.0.0", "--port", "9123",
                 "--project", "svc"]) == 0
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9123
    assert captured["app"].state.default_project == "svc"


def test_serve_uses_the_default_database_when_not_given(monkeypatch, tmp_path):
    import uvicorn

    monkeypatch.chdir(tmp_path)
    captured = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.update(kw, app=app))
    assert main(["serve"]) == 0
    assert captured["app"].state.db_path == ".lens/lens.db"


# ----------------------------------------------------------------------- parser


def test_parser_requires_a_subcommand():
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2


def test_parser_exposes_every_documented_subcommand():
    parser = build_parser()
    actions = [a for a in parser._actions if a.dest == "command"]
    choices = set(actions[0].choices)
    assert choices == {"import", "stats", "report", "serve", "seed-demo"}


def test_defaults_are_sane():
    args = build_parser().parse_args(["stats"])
    assert args.top == 10
    assert args.min_runs == 3
    assert args.project is None
    assert args.db is None

    serve = build_parser().parse_args(["serve"])
    assert (serve.host, serve.port) == ("127.0.0.1", 8000)

    seed = build_parser().parse_args(["seed-demo"])
    assert (seed.runs, seed.seed) == (14, 7)


# ------------------------------------------------------------------ 边界与空数据


def test_stats_reports_a_project_that_has_no_runs(populated_db, capsys):
    """显式指定一个不存在的项目时，要明确退出码 1，而不是崩掉。"""
    assert main(["stats", "--db", populated_db, "--project", "ghost"]) == 1
    assert "项目：ghost" in capsys.readouterr().out


def test_stats_says_so_when_nothing_is_flaky(tmp_path, capsys):
    from lens.models import CaseResult, RunInfo

    db = tmp_path / "calm.db"
    with Store(db) as store:
        for _ in range(3):
            store.record_run(
                RunInfo(project="calm", duration=0.2),
                [CaseResult(nodeid="t.py::test_ok", outcome="passed", duration=0.1)],
            )

    assert main(["stats", "--db", str(db), "--project", "calm"]) == 0
    out = capsys.readouterr().out
    assert "未发现不稳定用例" in out
    assert "最慢用例" in out
    assert "失败聚类" not in out


def test_report_on_a_project_with_no_runs(tmp_path, capsys):
    db = tmp_path / "empty.db"
    assert main(["report", "--db", str(db), "--project", "ghost"]) == 0
    assert "暂无运行记录" in capsys.readouterr().out
