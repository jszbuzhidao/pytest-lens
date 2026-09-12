"""pytest 插件：采集必须显式开启，采集结果必须能落库。"""

import pytest

from lens.plugin import _safe_longrepr, _truncate, _truthy, is_enabled, resolve_db, resolve_project
from lens.store import Store

PROBE = """
def test_ok():
    assert 1 + 1 == 2


def test_bad():
    assert 2 + 2 == 5
"""


class FakeConfig:
    """只实现插件真正用到的那两三个属性，避免拉起整个 pytest 会话。"""

    def __init__(self, rootpath, **options):
        self.rootpath = rootpath
        self._options = options

    def getoption(self, name, default=None):
        return self._options.get(name, default)


# ------------------------------------------------------------------ 纯函数部分


@pytest.mark.parametrize("value", ["1", "true", "True", "YES", "on", " on "])
def test_truthy_accepts_common_switch_spellings(value):
    assert _truthy(value) is True


@pytest.mark.parametrize("value", ["", None, "0", "false", "no", "off", "maybe"])
def test_truthy_rejects_everything_else(value):
    assert _truthy(value) is False


def test_truncate_leaves_short_text_alone():
    assert _truncate("abc", 10) == "abc"


def test_truncate_marks_what_it_cut_off():
    out = _truncate("x" * 20, 5)
    assert out.startswith("xxxxx")
    assert out.endswith("(truncated)")


def test_safe_longrepr_survives_a_hostile_report_object():
    class Hostile:
        @property
        def longrepr(self):
            raise RuntimeError("boom")

    assert _safe_longrepr(Hostile()) == ""


def test_is_enabled_follows_the_command_line_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("LENS_ENABLE", raising=False)
    assert is_enabled(FakeConfig(tmp_path, lens_enable=True)) is True
    assert is_enabled(FakeConfig(tmp_path, lens_enable=False)) is False


def test_is_enabled_follows_the_environment_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("LENS_ENABLE", "1")
    assert is_enabled(FakeConfig(tmp_path, lens_enable=False)) is True


def test_resolve_db_defaults_under_the_rootdir(tmp_path, monkeypatch):
    monkeypatch.delenv("LENS_DB", raising=False)
    assert resolve_db(FakeConfig(tmp_path, lens_db=None)) == tmp_path / ".lens" / "lens.db"


def test_resolve_db_prefers_the_explicit_option(tmp_path, monkeypatch):
    monkeypatch.delenv("LENS_DB", raising=False)
    cfg = FakeConfig(tmp_path, lens_db=str(tmp_path / "custom.db"))
    assert resolve_db(cfg) == tmp_path / "custom.db"


def test_resolve_db_falls_back_to_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LENS_DB", str(tmp_path / "env.db"))
    assert resolve_db(FakeConfig(tmp_path, lens_db=None)) == tmp_path / "env.db"


def test_resolve_project_defaults_to_the_directory_name(tmp_path, monkeypatch):
    monkeypatch.delenv("LENS_PROJECT", raising=False)
    monkeypatch.delenv("LENS_DB", raising=False)
    root = tmp_path / "my-service"
    root.mkdir()
    assert resolve_project(FakeConfig(root, lens_project=None)) == "my-service"


def test_resolve_project_prefers_the_explicit_option(tmp_path, monkeypatch):
    monkeypatch.delenv("LENS_PROJECT", raising=False)
    assert resolve_project(FakeConfig(tmp_path, lens_project="given")) == "given"


def test_resolve_project_falls_back_to_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LENS_PROJECT", "from-env")
    assert resolve_project(FakeConfig(tmp_path, lens_project=None)) == "from-env"


# ------------------------------------------------------------------ 会话级行为


def test_plugin_is_opt_in_and_writes_nothing_by_default(pytester, monkeypatch):
    monkeypatch.delenv("LENS_ENABLE", raising=False)
    pytester.makepyfile(test_probe=PROBE)
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, failed=1)
    assert not (pytester.path / ".lens").exists()


def test_plugin_records_a_run_when_asked(pytester, tmp_path, monkeypatch):
    monkeypatch.delenv("LENS_ENABLE", raising=False)
    pytester.makepyfile(test_probe=PROBE)
    db = tmp_path / "lens.db"

    result = pytester.runpytest(
        "-p", "no:cacheprovider",
        "--lens", "--lens-db", str(db), "--lens-project", "probe",
    )

    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*pytest-lens*", "*记录 2 个用例*"])

    with Store(db) as store:
        runs = store.runs("probe")
        assert len(runs) == 1
        assert runs[0]["total"] == 2
        assert runs[0]["failed"] == 1
        detail = store.run_detail(runs[0]["id"])

    bad = next(c for c in detail["cases"] if c["nodeid"].endswith("test_bad"))
    assert bad["outcome"] == "failed"
    assert bad["fingerprint"], "失败的用例应该带上指纹，否则聚类功能无从谈起"
    assert bad["message"]


def test_plugin_can_be_switched_on_by_environment(pytester, tmp_path, monkeypatch):
    monkeypatch.setenv("LENS_ENABLE", "1")
    pytester.makepyfile(test_probe="def test_ok():\n    assert True\n")
    db = tmp_path / "env.db"

    result = pytester.runpytest(
        "-p", "no:cacheprovider",
        "--lens-db", str(db), "--lens-project", "envprobe",
    )

    result.assert_outcomes(passed=1)
    assert "采集已开启" in result.stdout.str()

    with Store(db) as store:
        assert store.runs("envprobe")[0]["passed"] == 1


def test_plugin_maps_a_broken_fixture_to_an_error(pytester, tmp_path, monkeypatch):
    monkeypatch.delenv("LENS_ENABLE", raising=False)
    pytester.makepyfile(
        test_broken="""
        import pytest


        @pytest.fixture
        def broken():
            raise RuntimeError("fixture exploded")


        def test_needs_fixture(broken):
            pass
        """
    )
    db = tmp_path / "err.db"

    result = pytester.runpytest(
        "-p", "no:cacheprovider",
        "--lens", "--lens-db", str(db), "--lens-project", "errprobe",
    )

    result.assert_outcomes(errors=1)
    with Store(db) as store:
        run = store.run_detail(store.runs("errprobe")[0]["id"])["run"]
        assert run["errored"] == 1
        assert run["failed"] == 0


def test_plugin_counts_skips_without_breaking_the_pass_rate(pytester, tmp_path, monkeypatch):
    monkeypatch.delenv("LENS_ENABLE", raising=False)
    pytester.makepyfile(
        test_skips="""
        import pytest


        def test_runs():
            assert True


        @pytest.mark.skip(reason="later")
        def test_skipped():
            assert False
        """
    )
    db = tmp_path / "skip.db"

    pytester.runpytest(
        "-p", "no:cacheprovider",
        "--lens", "--lens-db", str(db), "--lens-project", "skipprobe",
    )

    with Store(db) as store:
        run = store.runs("skipprobe", limit=1)[0]
    assert (run["total"], run["passed"], run["skipped"]) == (2, 1, 1)
    assert run["pass_rate"] == pytest.approx(1.0)


def test_plugin_prunes_old_runs_beyond_the_keep_limit(pytester, tmp_path, monkeypatch):
    monkeypatch.delenv("LENS_ENABLE", raising=False)
    pytester.makepyfile(test_probe="def test_ok():\n    assert True\n")
    db = tmp_path / "keep.db"

    for _ in range(3):
        pytester.runpytest(
            "-p", "no:cacheprovider",
            "--lens", "--lens-db", str(db),
            "--lens-project", "keepprobe", "--lens-keep", "2",
        )

    with Store(db) as store:
        assert len(store.runs("keepprobe", limit=99)) == 2


def test_collector_stays_quiet_when_the_session_never_started(tmp_path, capsys):
    """collector 在没有 store、也没有记录时必须安静退出，不能抛异常拖垮整个会话。"""
    from lens.plugin import LensCollector

    config = FakeConfig(tmp_path, lens_db=None, lens_project=None, lens_keep=50)
    collector = LensCollector(config)

    collector.pytest_sessionfinish(None, 0)
    collector.pytest_terminal_summary(None)

    assert collector.store is None
    assert collector.run_id is None
    assert capsys.readouterr().out == ""
