"""pytest 插件：采集每次运行的用例结果并落库。

用法::

    pytest --lens                        # 结果写入 .lens/lens.db
    pytest --lens --lens-project myproj  # 指定项目名（多项目共用一个库时）
    LENS_ENABLE=1 pytest                 # 也可以在 CI 里用环境变量打开
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from .fingerprint import signature
from .models import (
    OUTCOME_ERROR,
    OUTCOME_FAILED,
    OUTCOME_PASSED,
    OUTCOME_SKIPPED,
    CaseResult,
    RunInfo,
    env_fingerprint,
    git_info,
    utcnow,
)
from .store import Store

STATE_ATTR = "_pytest_lens_state"


def pytest_addoption(parser) -> None:
    group = parser.getgroup("lens", "pytest-lens 结果采集")
    group.addoption(
        "--lens",
        action="store_true",
        default=False,
        dest="lens_enable",
        help="开启结果采集，写入 SQLite（等同于环境变量 LENS_ENABLE=1）",
    )
    group.addoption(
        "--lens-db",
        action="store",
        default=None,
        dest="lens_db",
        metavar="PATH",
        help="数据库路径，默认 <rootdir>/.lens/lens.db",
    )
    group.addoption(
        "--lens-project",
        action="store",
        default=None,
        dest="lens_project",
        metavar="NAME",
        help="项目名，默认取 rootdir 目录名",
    )
    group.addoption(
        "--lens-keep",
        action="store",
        type=int,
        default=50,
        dest="lens_keep",
        metavar="N",
        help="每个项目只保留最近 N 次运行，默认 50",
    )


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def is_enabled(config) -> bool:
    if config.getoption("lens_enable", False):
        return True
    return _truthy(os.environ.get("LENS_ENABLE"))


def resolve_db(config) -> Path:
    explicit = config.getoption("lens_db", None) or os.environ.get("LENS_DB")
    if explicit:
        return Path(explicit).expanduser()
    return Path(str(config.rootpath)) / ".lens" / "lens.db"


def resolve_project(config) -> str:
    return (
        config.getoption("lens_project", None)
        or os.environ.get("LENS_PROJECT")
        or Path(str(config.rootpath)).name
    )


class LensCollector:
    """挂在 config.pluginmanager 上的采集器。

    用实例而不是模块级全局变量，这样同进程内跑多个 pytest 会话（pytester 测试、
    pytest-xdist 等）不会互相污染状态。
    """

    def __init__(self, config) -> None:
        self.config = config
        self.db_path = resolve_db(config)
        self.project = resolve_project(config)
        self.keep = int(config.getoption("lens_keep", 50) or 50)
        self.store: Store | None = None
        self.records: dict[str, dict] = {}
        self.run_id: int | None = None
        self._t0 = 0.0

    # ------------------------------------------------------------ hooks

    def pytest_sessionstart(self, session) -> None:
        self._t0 = time.perf_counter()
        self.store = Store(self.db_path)

    @pytest.hookimpl(trylast=True)
    def pytest_runtest_logreport(self, report) -> None:
        rec = self.records.setdefault(
            report.nodeid,
            {"nodeid": report.nodeid, "outcomes": {}, "duration": 0.0, "longrepr": "", "when": ""},
        )
        rec["duration"] += float(report.duration or 0.0)
        rec["outcomes"][report.when] = report.outcome
        if report.outcome in ("failed", "error") and not rec["longrepr"]:
            rec["longrepr"] = _safe_longrepr(report)
            rec["when"] = report.when

    def pytest_sessionfinish(self, session, exitstatus) -> None:
        if self.store is None:
            return
        try:
            cases = [self._finalize(rec) for rec in self.records.values()]
            run = self._build_run(cases)
            self.run_id = self.store.record_run(run, cases)
            self.store.prune(self.project, keep=self.keep)
            self._last_run = run
        finally:
            self.store.close()
            self.store = None

    def pytest_terminal_summary(self, terminalreporter) -> None:
        run = getattr(self, "_last_run", None)
        if run is None:
            return
        terminalreporter.write_sep("-", "pytest-lens")
        terminalreporter.write_line(
            "记录 %d 个用例：通过 %d / 失败 %d / 错误 %d / 跳过 %d，通过率 %.1f%%，用时 %.2fs"
            % (
                run.total,
                run.passed,
                run.failed,
                run.errored,
                run.skipped,
                run.pass_rate * 100,
                run.duration,
            )
        )
        terminalreporter.write_line("数据库：%s（run #%s，项目 %s）" % (self.db_path, self.run_id, self.project))

    # ------------------------------------------------------------ 内部

    def _build_run(self, cases: list[CaseResult]) -> RunInfo:
        rootdir = Path(str(self.config.rootpath))
        commit, branch = git_info(rootdir)
        args = getattr(self.config, "invocation_params", None)
        command = " ".join(getattr(args, "args", []) or [])
        run = RunInfo(
            project=self.project,
            started_at=utcnow(),
            finished_at=utcnow(),
            duration=time.perf_counter() - self._t0,
            git_commit=commit or os.environ.get("GITHUB_SHA", "")[:7] or None,
            git_branch=branch or os.environ.get("GITHUB_REF_NAME") or None,
            ci_build=os.environ.get("GITHUB_RUN_ID") or os.environ.get("CI_BUILD_ID") or None,
            env=env_fingerprint(),
            command=command,
        )
        return run.tally(cases)

    @staticmethod
    def _finalize(rec: dict) -> CaseResult:
        when = rec["outcomes"]
        if when.get("setup") == "failed" or when.get("teardown") == "failed":
            outcome = OUTCOME_ERROR
        elif when.get("call") == "failed":
            outcome = OUTCOME_FAILED
        elif when.get("call") == "passed":
            outcome = OUTCOME_PASSED
        else:
            outcome = OUTCOME_SKIPPED

        longrepr = rec["longrepr"]
        fp = ""
        label = ""
        if outcome in (OUTCOME_FAILED, OUTCOME_ERROR):
            sig = signature(longrepr)
            fp, label = sig.fingerprint, sig.label
        return CaseResult(
            nodeid=rec["nodeid"],
            outcome=outcome,
            duration=rec["duration"],
            message=label,
            longrepr=_truncate(longrepr, 4000),
            fingerprint=fp,
            signature=label,
        )


def _safe_longrepr(report) -> str:
    try:
        return str(report.longrepr)
    except Exception:  # pragma: no cover - longrepr 极少数情况下会抛
        return ""


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n... (truncated)"


@pytest.hookimpl(trylast=True)
def pytest_configure(config) -> None:
    if not is_enabled(config):
        return
    collector = LensCollector(config)
    setattr(config, STATE_ATTR, collector)
    config.pluginmanager.register(collector, "lens-collector")


def pytest_unconfigure(config) -> None:
    collector = getattr(config, STATE_ATTR, None)
    if collector is not None:
        config.pluginmanager.unregister(collector)
        setattr(config, STATE_ATTR, None)


def pytest_report_header(config) -> str | None:
    if is_enabled(config):
        return "pytest-lens: 采集已开启 -> %s" % resolve_db(config)
    return None
