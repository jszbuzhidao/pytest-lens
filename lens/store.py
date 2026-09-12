"""SQLite 存储层：写入一次运行结果，并在上面做聚合查询。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .flaky import classify_all, only_flaky
from .models import CaseResult, RunInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project      TEXT    NOT NULL,
    started_at   TEXT    NOT NULL,
    finished_at  TEXT,
    duration     REAL    NOT NULL DEFAULT 0,
    git_commit   TEXT,
    git_branch   TEXT,
    ci_build     TEXT,
    env          TEXT    NOT NULL DEFAULT '',
    command      TEXT    NOT NULL DEFAULT '',
    total        INTEGER NOT NULL DEFAULT 0,
    passed       INTEGER NOT NULL DEFAULT 0,
    failed       INTEGER NOT NULL DEFAULT 0,
    errored      INTEGER NOT NULL DEFAULT 0,
    skipped      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project, id DESC);

CREATE TABLE IF NOT EXISTS cases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    nodeid      TEXT    NOT NULL,
    file        TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    outcome     TEXT    NOT NULL,
    duration    REAL    NOT NULL DEFAULT 0,
    message     TEXT    NOT NULL DEFAULT '',
    longrepr    TEXT    NOT NULL DEFAULT '',
    fingerprint TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_cases_run  ON cases(run_id);
CREATE INDEX IF NOT EXISTS idx_cases_node ON cases(nodeid, run_id DESC);
CREATE INDEX IF NOT EXISTS idx_cases_fp   ON cases(fingerprint);
"""


def _rows(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


class Store:
    def __init__(self, path: str | Path = ".lens/lens.db") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- 写入

    def record_run(self, run: RunInfo, cases: list[CaseResult]) -> int:
        run.tally(cases)
        cur = self.conn.execute(
            """INSERT INTO runs (project, started_at, finished_at, duration,
                                 git_commit, git_branch, ci_build, env, command,
                                 total, passed, failed, errored, skipped)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            run.to_row(),
        )
        run_id = int(cur.lastrowid or 0)
        self.conn.executemany(
            """INSERT OR REPLACE INTO cases
               (run_id, nodeid, file, name, outcome, duration, message, longrepr, fingerprint)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [c.to_row(run_id) for c in cases],
        )
        self.conn.commit()
        return run_id

    def prune(self, project: str, keep: int = 50) -> int:
        """只保留最近 keep 次运行，返回删掉的运行数。"""
        cur = self.conn.execute(
            """DELETE FROM runs WHERE project = ? AND id NOT IN (
                   SELECT id FROM runs WHERE project = ? ORDER BY id DESC LIMIT ?)""",
            (project, project, keep),
        )
        self.conn.commit()
        return cur.rowcount or 0

    # ---------------------------------------------------------------- 读取

    def projects(self) -> list[dict]:
        return _rows(
            self.conn.execute(
                """SELECT project,
                          COUNT(*)      AS runs,
                          MAX(started_at) AS last_run_at
                     FROM runs GROUP BY project ORDER BY last_run_at DESC"""
            )
        )

    def runs(self, project: str | None = None, limit: int = 50) -> list[dict]:
        sql = """SELECT id, project, started_at, finished_at, duration, git_commit,
                        git_branch, ci_build, env, command,
                        total, passed, failed, errored, skipped
                   FROM runs"""
        args: list = []
        if project:
            sql += " WHERE project = ?"
            args.append(project)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        out = _rows(self.conn.execute(sql, args))
        for r in out:
            executed = r["total"] - r["skipped"]
            r["pass_rate"] = (r["passed"] / executed) if executed > 0 else 0.0
        return out

    def run_detail(self, run_id: int) -> dict:
        run = _rows(self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)))
        if not run:
            raise KeyError(f"run {run_id} not found")
        cases = _rows(
            self.conn.execute(
                """SELECT nodeid, file, name, outcome, duration, message, fingerprint
                     FROM cases WHERE run_id = ?
                    ORDER BY CASE outcome WHEN 'error' THEN 0 WHEN 'failed' THEN 1
                                          WHEN 'skipped' THEN 3 ELSE 2 END,
                             duration DESC""",
                (run_id,),
            )
        )
        return {"run": run[0], "cases": cases}

    def summary(self, project: str) -> dict:
        runs = self.runs(project, limit=2)
        if not runs:
            return {"project": project, "latest": None, "previous": None, "delta": {}}
        latest = runs[0]
        previous = runs[1] if len(runs) > 1 else None
        delta = {}
        if previous:
            delta = {
                "passed": latest["passed"] - previous["passed"],
                "failed": latest["failed"] - previous["failed"],
                "errored": latest["errored"] - previous["errored"],
                "pass_rate": latest["pass_rate"] - previous["pass_rate"],
                "duration": latest["duration"] - previous["duration"],
            }
        return {
            "project": project,
            "latest": latest,
            "previous": previous,
            "delta": delta,
            "flaky": len(self.flaky(project)),
        }

    def trend(self, project: str, limit: int = 30) -> list[dict]:
        rows = self.runs(project, limit=limit)
        rows.reverse()
        return rows

    def slowest(self, project: str, limit: int = 10, last_runs: int = 1) -> list[dict]:
        return _rows(
            self.conn.execute(
                """SELECT c.nodeid, c.file, c.outcome, c.duration, c.message
                     FROM cases c JOIN runs r ON r.id = c.run_id
                    WHERE r.project = ? AND r.id IN (
                          SELECT id FROM runs WHERE project = ? ORDER BY id DESC LIMIT ?)
                      AND c.outcome != 'skipped'
                    ORDER BY c.duration DESC LIMIT ?""",
                (project, project, int(last_runs), int(limit)),
            )
        )

    def failure_clusters(self, project: str, limit: int = 20, last_runs: int = 5) -> list[dict]:
        rows = _rows(
            self.conn.execute(
                """SELECT c.fingerprint,
                          COUNT(*)                       AS occurrences,
                          COUNT(DISTINCT c.nodeid)       AS cases,
                          MAX(c.message)                 AS message,
                          MIN(c.file)                    AS file,
                          MAX(r.started_at)              AS last_seen,
                          GROUP_CONCAT(DISTINCT c.nodeid) AS nodeids
                     FROM cases c JOIN runs r ON r.id = c.run_id
                    WHERE r.project = ?
                      AND c.outcome IN ('failed','error')
                      AND c.fingerprint != ''
                      AND r.id IN (SELECT id FROM runs WHERE project = ? ORDER BY id DESC LIMIT ?)
                    GROUP BY c.fingerprint
                    ORDER BY occurrences DESC, last_seen DESC
                    LIMIT ?""",
                (project, project, int(last_runs), int(limit)),
            )
        )
        for r in rows:
            names = (r.pop("nodeids") or "").split(",")
            r["sample_cases"] = sorted({n for n in names if n})[:5]
        return rows

    def flaky(self, project: str, min_runs: int = 3, limit: int = 20, window: int = 20) -> list[dict]:
        """不稳定用例：在最近 window 次运行中既通过又失败过。

        判定逻辑集中在 flaky.classify_all，避免 SQL 和 Python 两套实现走偏。
        """
        pairs = [
            (r["nodeid"], r["outcome"])
            for r in self.conn.execute(
                """SELECT c.nodeid, c.outcome
                     FROM cases c JOIN runs r ON r.id = c.run_id
                    WHERE r.project = ?
                      AND r.id IN (SELECT id FROM runs WHERE project = ? ORDER BY id DESC LIMIT ?)""",
                (project, project, int(window)),
            )
        ]
        verdicts = only_flaky(classify_all(pairs, min_runs=min_runs))
        return [v.as_dict() for v in verdicts[: int(limit)]]

    def case_history(self, nodeid: str, limit: int = 20) -> list[dict]:
        return _rows(
            self.conn.execute(
                """SELECT r.id AS run_id, r.project, r.started_at, c.outcome, c.duration
                     FROM cases c JOIN runs r ON r.id = c.run_id
                    WHERE c.nodeid = ? ORDER BY r.id DESC LIMIT ?""",
                (nodeid, int(limit)),
            )
        )
