"""数据模型：nodeid 解析、统计与通过率口径。"""

import sys
from datetime import datetime, timedelta, timezone

import pytest

from lens.models import (
    ALL_OUTCOMES,
    CaseResult,
    RunInfo,
    env_fingerprint,
    first_line,
    git_info,
    iso,
    python_executable,
    split_nodeid,
)


@pytest.mark.parametrize(
    "nodeid,file,name",
    [
        ("tests/test_a.py::test_x", "tests/test_a.py", "test_x"),
        ("tests/test_a.py::TestX::test_y[p1]", "tests/test_a.py", "TestX::test_y[p1]"),
        ("standalone", "standalone", "standalone"),
    ],
)
def test_split_nodeid(nodeid, file, name):
    assert split_nodeid(nodeid) == (file, name)


def test_first_line_skips_blanks_and_strips_pytest_prefix():
    assert first_line("\n\nE       assert 1 == 2\nmore") == "assert 1 == 2"
    assert first_line(None) == ""
    assert first_line("") == ""


def test_first_line_returns_empty_when_the_text_has_no_content():
    assert first_line("\n\n   \n") == ""


def test_iso_marks_naive_datetime_as_utc():
    assert iso(datetime(2026, 9, 12, 4, 0, 0)).endswith("+00:00")
    assert iso(None) is None


def test_case_result_rejects_unknown_outcome():
    with pytest.raises(ValueError):
        CaseResult(nodeid="tests/test_a.py::test_x", outcome="exploded")


def test_case_result_splits_file_and_name():
    case = CaseResult(nodeid="tests/test_a.py::TestX::test_y", outcome="failed")
    assert case.file == "tests/test_a.py"
    assert case.name == "TestX::test_y"
    assert case.failed is True


def test_runinfo_pass_rate_excludes_skipped():
    cases = [
        CaseResult("t.py::a", "passed"),
        CaseResult("t.py::b", "failed"),
        CaseResult("t.py::c", "skipped"),
        CaseResult("t.py::d", "skipped"),
    ]
    run = RunInfo(project="p").tally(cases)
    assert (run.total, run.passed, run.failed, run.skipped) == (4, 1, 1, 2)
    assert run.pass_rate == pytest.approx(0.5)


def test_runinfo_pass_rate_is_zero_when_everything_skipped():
    run = RunInfo(project="p").tally([CaseResult("t.py::a", "skipped")])
    assert run.pass_rate == 0.0


def test_runinfo_errors_are_counted_separately_from_failures():
    cases = [CaseResult("t.py::a", "error"), CaseResult("t.py::b", "failed")]
    run = RunInfo(project="p").tally(cases)
    assert (run.failed, run.errored) == (1, 1)


def test_all_outcomes_is_the_single_source_of_truth():
    assert set(ALL_OUTCOMES) == {"passed", "failed", "error", "skipped"}


def test_case_result_passed_is_not_failed():
    assert CaseResult("t.py::a", "passed").failed is False
    assert CaseResult("t.py::a", "error").failed is True


def test_case_result_to_row_matches_the_schema_order():
    case = CaseResult(
        nodeid="tests/test_a.py::TestX::test_y",
        outcome="failed",
        duration=0.5,
        message="m",
        longrepr="lr",
        fingerprint="fp",
    )
    row = case.to_row(7)
    assert row == (7, "tests/test_a.py::TestX::test_y", "tests/test_a.py", "TestX::test_y", "failed", 0.5, "m", "lr", "fp")


def test_case_result_to_row_survives_a_none_duration():
    assert CaseResult("t.py::a", "passed", duration=None).to_row(1)[5] == 0.0


def test_runinfo_to_row_matches_the_schema_order():
    run = RunInfo(project="p").tally([CaseResult("t.py::a", "passed")])
    row = run.to_row()
    assert len(row) == 14
    assert row[0] == "p"
    assert row[9:14] == (1, 1, 0, 0, 0)


def test_runinfo_as_dict_is_json_friendly():
    run = RunInfo(project="p").tally([CaseResult("t.py::a", "passed")])
    data = run.as_dict()
    assert data["project"] == "p"
    assert data["pass_rate"] == 1.0
    assert isinstance(data["started_at"], str)
    assert data["finished_at"] is None


def test_iso_normalises_an_aware_datetime_to_utc():
    tz = timezone(timedelta(hours=8))
    assert iso(datetime(2026, 9, 12, 12, 0, 0, tzinfo=tz)) == "2026-09-12T04:00:00+00:00"


def test_first_line_truncates_to_the_limit():
    assert first_line("x" * 300, limit=10) == "x" * 10


def test_first_line_normalises_crlf():
    assert first_line("first\r\nsecond") == "first"


# ---------------------------------------------------------------- 环境与 git 信息


def test_env_fingerprint_names_the_interpreter_and_pytest():
    text = env_fingerprint()
    assert text.startswith("python ")
    assert "pytest" in text


def test_python_executable_is_the_running_interpreter():
    assert python_executable() == sys.executable


class _FakeCompleted:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_git_info_reads_commit_and_branch(tmp_path, monkeypatch):
    monkeypatch.setattr("lens.models.subprocess.run", lambda *a, **k: _FakeCompleted("abc1234\n"))
    assert git_info(tmp_path) == ("abc1234", "abc1234")


def test_git_info_returns_none_when_git_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr("lens.models.subprocess.run", lambda *a, **k: _FakeCompleted("", 128))
    assert git_info(tmp_path) == (None, None)


def test_git_info_returns_none_on_empty_output(tmp_path, monkeypatch):
    monkeypatch.setattr("lens.models.subprocess.run", lambda *a, **k: _FakeCompleted("   \n"))
    assert git_info(tmp_path) == (None, None)


def test_git_info_returns_none_when_git_is_not_installed(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr("lens.models.subprocess.run", boom)
    assert git_info(tmp_path) == (None, None)


def test_git_info_returns_none_outside_a_repository(tmp_path):
    """真实调用一次：一个不存在的目录里 git 必然失败。"""
    assert git_info(tmp_path / "definitely-does-not-exist") == (None, None)
