import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest_plugins = ["pytester"]

CART_FAIL = (
    "___________________________ test_total ___________________________\n\n"
    "E       AssertionError: expected 3, got 7\n\n"
    "tests/test_cart.py:12: AssertionError"
)
CART_FAIL_LATER = (
    "___________________________ test_total ___________________________\n\n"
    "E       AssertionError: expected 5, got 9\n\n"
    "tests/test_cart.py:31: AssertionError"
)
WOBBLE_FAIL = (
    "___________________________ test_wobble ___________________________\n\n"
    "E       TimeoutError: gateway did not respond in 1200ms\n\n"
    "tests/test_checkout.py:20: TimeoutError"
)


@pytest.fixture
def store(tmp_path):
    from lens.store import Store

    with Store(tmp_path / "lens.db") as s:
        yield s


@pytest.fixture
def demo_db(tmp_path):
    """写入 3 次演示运行，供 API / CLI 测试用。"""
    from lens.cli import main

    db = tmp_path / "demo.db"
    assert main(["seed-demo", "--db", str(db), "--runs", "3"]) == 0
    return str(db)


@pytest.fixture
def populated_db(tmp_path):
    """确定性数据：3 次运行，含 1 个失败聚类、2 个 flaky 用例、1 个跳过。

    项目名 svc；数据全部手写，保证断言不依赖随机数。
    """
    from lens.fingerprint import signature
    from lens.models import CaseResult, RunInfo
    from lens.store import Store

    def failed(nodeid, raw, duration):
        sig = signature(raw)
        return CaseResult(
            nodeid=nodeid,
            outcome="failed",
            duration=duration,
            message=sig.label,
            longrepr=raw,
            fingerprint=sig.fingerprint,
            signature=sig.label,
        )

    def passed(nodeid, duration):
        return CaseResult(nodeid=nodeid, outcome="passed", duration=duration)

    runs = [
        [
            passed("tests/test_cart.py::test_ok", 0.10),
            failed("tests/test_cart.py::test_total", CART_FAIL, 0.12),
            passed("tests/test_checkout.py::test_wobble", 0.30),
        ],
        [
            passed("tests/test_cart.py::test_ok", 0.11),
            passed("tests/test_cart.py::test_total", 0.90),
            failed("tests/test_checkout.py::test_wobble", WOBBLE_FAIL, 2.50),
        ],
        [
            passed("tests/test_cart.py::test_ok", 0.09),
            failed("tests/test_cart.py::test_total", CART_FAIL_LATER, 0.40),
            passed("tests/test_checkout.py::test_wobble", 0.20),
            CaseResult(nodeid="tests/test_cart.py::test_never", outcome="skipped"),
        ],
    ]

    db = tmp_path / "svc.db"
    with Store(db) as s:
        for i, cases in enumerate(runs, start=1):
            s.record_run(
                RunInfo(
                    project="svc",
                    duration=round(1.0 + i, 2),
                    git_commit=f"c0mm1t{i}",
                    git_branch="main",
                    ci_build=str(100 + i),
                    env="py3.13 | pytest 9",
                    command="pytest --lens",
                ),
                cases,
            )
    return str(db)
