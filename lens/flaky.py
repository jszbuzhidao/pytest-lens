"""不稳定用例（flaky test）判定。

flaky 的定义：在足够多的历史运行里**既通过过也失败过**。
纯手工测试不会遇到这个问题，但只要上了 CI 它就会吃掉大量排查时间 ——
所以能自动把 flaky 挑出来，是测试平台最实用的功能之一。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

FLAKY = "flaky"
STABLE_PASS = "stable-pass"
STABLE_FAIL = "stable-fail"
INSUFFICIENT = "insufficient-data"

FAILED_OUTCOMES = ("failed", "error")


@dataclass
class FlakyVerdict:
    nodeid: str
    appearances: int
    passes: int
    fails: int
    min_runs: int

    @property
    def flaky_score(self) -> float:
        total = self.passes + self.fails
        return round(self.fails / total, 3) if total else 0.0

    @property
    def verdict(self) -> str:
        if self.appearances < self.min_runs:
            return INSUFFICIENT
        if self.passes and self.fails:
            return FLAKY
        return STABLE_FAIL if self.fails else STABLE_PASS

    @property
    def is_flaky(self) -> bool:
        return self.verdict == FLAKY

    def as_dict(self) -> dict:
        return {
            "nodeid": self.nodeid,
            "appearances": self.appearances,
            "passes": self.passes,
            "fails": self.fails,
            "flaky_score": self.flaky_score,
            "verdict": self.verdict,
        }


def classify(nodeid: str, outcomes: Sequence[str], min_runs: int = 3) -> FlakyVerdict:
    passes = sum(1 for o in outcomes if o == "passed")
    fails = sum(1 for o in outcomes if o in FAILED_OUTCOMES)
    appearances = passes + fails  # skipped 不计入：跳过不是「跑过」
    return FlakyVerdict(nodeid, appearances, passes, fails, min_runs)


def classify_all(
    histories: Mapping[str, Sequence[str]] | Iterable[tuple[str, str]],
    min_runs: int = 3,
) -> list[FlakyVerdict]:
    """按 nodeid 聚合结果序列，返回判定列表（flaky 排在前面）。"""
    grouped: dict[str, list[str]] = defaultdict(list)
    if isinstance(histories, Mapping):
        for nodeid, outcomes in histories.items():
            grouped[nodeid].extend(outcomes)
    else:
        for nodeid, outcome in histories:
            grouped[nodeid].append(outcome)

    verdicts = [classify(n, o, min_runs) for n, o in grouped.items()]
    order = {FLAKY: 0, STABLE_FAIL: 1, INSUFFICIENT: 2, STABLE_PASS: 3}
    verdicts.sort(key=lambda v: (order[v.verdict], -v.fails, v.nodeid))
    return verdicts


def only_flaky(verdicts: Iterable[FlakyVerdict]) -> list[FlakyVerdict]:
    return [v for v in verdicts if v.is_flaky]
