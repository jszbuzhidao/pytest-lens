"""Markdown 报告：能直接贴进 CI 日志或 PR 评论。"""

from lens.report import build_report
from lens.store import Store


def test_report_without_data_says_so(tmp_path):
    with Store(tmp_path / "empty.db") as store:
        text = build_report(store, "ghost")
    assert "# 测试质量报告 · ghost" in text
    assert "暂无运行记录" in text


def test_report_contains_the_overview_block(populated_db):
    with Store(populated_db) as store:
        text = build_report(store, "svc")
    assert "## 概览" in text
    assert "| 最近运行 | #3 @" in text
    assert "`c0mm1t3`" in text
    assert "| 通过率 | **" in text


def test_report_shows_the_delta_against_the_previous_run(populated_db):
    with Store(populated_db) as store:
        text = build_report(store, "svc")
    assert "与上一次相比" in text


def test_report_lists_the_trend_table(populated_db):
    with Store(populated_db) as store:
        text = build_report(store, "svc")
    assert "## 通过率趋势" in text
    assert "| #1 |" in text and "| #3 |" in text


def test_report_lists_slowest_cases(populated_db):
    with Store(populated_db) as store:
        text = build_report(store, "svc")
    assert "最慢用例 TOP" in text
    assert "test_wobble" in text


def test_report_groups_failures_into_clusters(populated_db):
    with Store(populated_db) as store:
        text = build_report(store, "svc")
    assert "## 失败聚类（同一指纹即同一个 bug）" in text


def test_report_calls_out_flaky_cases_when_present(populated_db):
    with Store(populated_db) as store:
        text = build_report(store, "svc")
    assert "## 不稳定用例（flaky）" in text
    assert "test_wobble" in text


def test_report_says_so_when_nothing_is_flaky(tmp_path):
    from lens.models import CaseResult, RunInfo

    with Store(tmp_path / "clean.db") as store:
        for _ in range(3):
            store.record_run(
                RunInfo(project="clean", duration=0.4),
                [CaseResult(nodeid="t.py::test_ok", outcome="passed", duration=0.1)],
            )
        text = build_report(store, "clean")
    assert "最近窗口内没有发现时好时坏的用例" in text


def test_report_of_a_single_run_omits_the_comparison_and_slowest_table(tmp_path):
    """只有一次运行、且全是跳过时：没有环比可谈，也没有「最慢用例」可排。"""
    from lens.models import CaseResult, RunInfo

    with Store(tmp_path / "once.db") as store:
        store.record_run(
            RunInfo(project="once", duration=0.3, git_commit="abc1234"),
            [CaseResult(nodeid="t.py::test_later", outcome="skipped")],
        )
        text = build_report(store, "once")
    assert "与上一次相比" not in text
    assert "最慢用例" not in text
    assert "## 通过率趋势" in text


def test_report_formatting_helpers():
    from lens.report import _delta, _pct

    assert _pct(0.1234) == "12.3%"
    assert _pct(0.5) == "50.0%"
    assert _pct(1.0) == "100.0%"
    assert _delta(None) == "—"
    assert _delta(3) == "+3"
    assert _delta(-3) == "-3"
    assert _delta(1.25, "s") == "+1.2s"
    assert _delta(-0.5, "%") == "-0.5%"


def test_report_honours_trend_and_top_limits(populated_db):
    with Store(populated_db) as store:
        text = build_report(store, "svc", trend_limit=1, top=1)
    trend_rows = [ln for ln in text.splitlines() if ln.startswith("| #")]
    assert len(trend_rows) == 1
