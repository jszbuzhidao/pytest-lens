"""数据模型：一次测试运行（RunInfo）与单个用例结果（CaseResult）。"""

from __future__ import annotations

import platform
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

OUTCOME_PASSED = "passed"
OUTCOME_FAILED = "failed"
OUTCOME_ERROR = "error"
OUTCOME_SKIPPED = "skipped"
ALL_OUTCOMES = (OUTCOME_PASSED, OUTCOME_FAILED, OUTCOME_ERROR, OUTCOME_SKIPPED)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def split_nodeid(nodeid: str) -> tuple[str, str]:
    """'tests/test_a.py::TestX::test_y[p1]' -> ('tests/test_a.py', 'TestX::test_y[p1]')"""
    if "::" in nodeid:
        file_part, _, rest = nodeid.partition("::")
        return file_part, rest
    return nodeid, nodeid


def first_line(text: str | None, limit: int = 200) -> str:
    if not text:
        return ""
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("E "):
            line = line[2:].strip()
        return line[:limit]
    return ""


@dataclass
class CaseResult:
    nodeid: str
    outcome: str
    duration: float = 0.0
    message: str = ""
    longrepr: str = ""
    fingerprint: str = ""
    signature: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in ALL_OUTCOMES:
            raise ValueError(f"unknown outcome: {self.outcome!r}")
        self.file, self.name = split_nodeid(self.nodeid)

    @property
    def failed(self) -> bool:
        return self.outcome in (OUTCOME_FAILED, OUTCOME_ERROR)

    def to_row(self, run_id: int) -> tuple:
        return (
            run_id,
            self.nodeid,
            self.file,
            self.name,
            self.outcome,
            float(self.duration or 0.0),
            self.message,
            self.longrepr,
            self.fingerprint,
        )


@dataclass
class RunInfo:
    project: str
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    duration: float = 0.0
    git_commit: str | None = None
    git_branch: str | None = None
    ci_build: str | None = None
    env: str = ""
    command: str = ""
    total: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0

    @property
    def pass_rate(self) -> float:
        executed = self.total - self.skipped
        return (self.passed / executed) if executed > 0 else 0.0

    def tally(self, cases: list[CaseResult]) -> "RunInfo":
        self.total = len(cases)
        self.passed = sum(1 for c in cases if c.outcome == OUTCOME_PASSED)
        self.failed = sum(1 for c in cases if c.outcome == OUTCOME_FAILED)
        self.errored = sum(1 for c in cases if c.outcome == OUTCOME_ERROR)
        self.skipped = sum(1 for c in cases if c.outcome == OUTCOME_SKIPPED)
        return self

    def to_row(self) -> tuple:
        return (
            self.project,
            iso(self.started_at) or "",
            iso(self.finished_at),
            float(self.duration or 0.0),
            self.git_commit,
            self.git_branch,
            self.ci_build,
            self.env,
            self.command,
            self.total,
            self.passed,
            self.failed,
            self.errored,
            self.skipped,
        )

    def as_dict(self) -> dict:
        data = asdict(self)
        data["started_at"] = iso(self.started_at)
        data["finished_at"] = iso(self.finished_at)
        data["pass_rate"] = self.pass_rate
        return data


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except Exception:
        return None
    return None


def git_info(cwd: str | Path | None = None) -> tuple[str | None, str | None]:
    """返回 (commit, branch)，取不到就返回 (None, None)。"""
    cwd = Path(cwd or Path.cwd())
    return _git(["rev-parse", "--short", "HEAD"], cwd), _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)


def env_fingerprint() -> str:
    """运行环境指纹：Python / pytest / 平台。不同机器上数据不可比时用得上。"""
    try:
        import pytest

        pytest_version = pytest.__version__
    except Exception:  # pragma: no cover - pytest 一定在
        pytest_version = "?"
    return "python {py} | pytest {pt} | {plat} {rel}".format(
        py=platform.python_version(),
        pt=pytest_version,
        plat=platform.system(),
        rel=platform.release(),
    )


def python_executable() -> str:
    return sys.executable
