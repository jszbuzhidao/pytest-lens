"""命令行入口：lens import / stats / report / serve / seed-demo"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import timedelta
from pathlib import Path

from .fingerprint import signature
from .junitxml import parse_junit
from .models import (
    OUTCOME_ERROR,
    OUTCOME_FAILED,
    OUTCOME_PASSED,
    OUTCOME_SKIPPED,
    CaseResult,
    RunInfo,
    env_fingerprint,
    utcnow,
)
from .report import build_report
from .store import Store

DEFAULT_DB = ".lens/lens.db"


def _store(args) -> Store:
    return Store(getattr(args, "db", None) or DEFAULT_DB)


def _pick_project(store: Store, explicit: str | None) -> str:
    if explicit:
        return explicit
    projects = store.projects()
    if not projects:
        print("数据库里没有任何运行记录。先跑 `pytest --lens` 或 `lens import <junit.xml>`。", file=sys.stderr)
        raise SystemExit(2)
    return projects[0]["project"]


# --------------------------------------------------------------------- import


def cmd_import(args) -> int:
    total_runs = 0
    for pattern in args.files:
        for path in sorted(Path().glob(pattern)) if any(ch in pattern for ch in "*?[") else [Path(pattern)]:
            if not path.exists():
                print(f"找不到文件：{path}", file=sys.stderr)
                continue
            run, cases = parse_junit(path)
            run.project = args.project or run.project
            if args.commit:
                run.git_commit = args.commit
            if args.branch:
                run.git_branch = args.branch
            with _store(args) as store:
                run_id = store.record_run(run, cases)
            total_runs += 1
            print(f"导入 {path} -> run #{run_id}（{run.total} 个用例，通过率 {run.pass_rate * 100:.1f}%）")
    if not total_runs:
        print("没有导入任何文件。", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------- stats


def cmd_stats(args) -> int:
    with _store(args) as store:
        project = _pick_project(store, args.project)
        summary = store.summary(project)
        latest = summary["latest"]
        print(f"项目：{project}")
        if not latest:
            return 1
        print(
            "最近一次 run #{id} @ {at}（commit {commit}）".format(
                id=latest["id"], at=latest["started_at"], commit=latest["git_commit"] or "-"
            )
        )
        print(
            "  用例 {total}：通过 {passed} / 失败 {failed} / 错误 {errored} / 跳过 {skipped}，通过率 {rate:.1f}%，用时 {dur:.2f}s".format(
                rate=latest["pass_rate"] * 100, dur=latest["duration"], **latest
            )
        )
        if summary.get("previous"):
            d = summary["delta"]
            print(
                "  环比：通过率 {:+.1f}%，失败 {:+d}，用时 {:+.2f}s".format(
                    (d.get("pass_rate") or 0) * 100, d.get("failed") or 0, d.get("duration") or 0
                )
            )

        clusters = store.failure_clusters(project, limit=args.top)
        if clusters:
            print(f"\n失败聚类（{len(clusters)} 类）：")
            for c in clusters:
                print(f"  [{c['fingerprint']}] x{c['occurrences']} 涉及 {c['cases']} 个用例  {c['message'][:70]}")

        flaky = store.flaky(project, min_runs=args.min_runs, limit=args.top)
        if flaky:
            print(f"\n不稳定用例（{len(flaky)} 个）：")
            for f in flaky:
                print(
                    "  {nodeid}  通过 {passes} / 失败 {fails}  flaky={score:.2f}".format(
                        score=f["flaky_score"], **f
                    )
                )
        else:
            print("\n未发现不稳定用例。")

        slow = store.slowest(project, limit=min(5, args.top))
        if slow:
            print("\n最慢用例：")
            for c in slow:
                print(f"  {c['duration']:.3f}s  {c['nodeid']}")
    return 0


# --------------------------------------------------------------------- report


def cmd_report(args) -> int:
    with _store(args) as store:
        project = _pick_project(store, args.project)
        text = build_report(store, project, trend_limit=args.trend, top=args.top)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}")
    else:
        print(text)
    return 0


# ---------------------------------------------------------------------- serve


def cmd_serve(args) -> int:
    try:
        import uvicorn
    except ImportError:  # pragma: no cover
        print("需要先安装 web 依赖：pip install 'pytest-lens[web]'", file=sys.stderr)
        return 1
    from .api import create_app

    app = create_app(args.db or DEFAULT_DB, default_project=args.project)
    print(f"pytest-lens 看板： http://{args.host}:{args.port}/  （数据库 {args.db or DEFAULT_DB}）")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


# ------------------------------------------------------------------ seed-demo


_DEMO_SUITE = {
    "tests/test_cart.py": [
        "test_add_item", "test_add_same_item_twice", "test_remove_item",
        "test_remove_missing_item", "test_total_price", "test_empty_cart_total",
        "test_clear_cart", "test_max_quantity",
    ],
    "tests/test_pricing.py": [
        "test_discount_10_percent", "test_discount_stacking", "test_coupon_expired",
        "test_coupon_unknown", "test_vip_price", "test_price_rounding",
    ],
    "tests/test_checkout.py": [
        "test_checkout_success", "test_checkout_empty_cart", "test_checkout_stock_shortage",
        "test_checkout_payment_retry", "test_checkout_idempotent", "test_checkout_address_validation",
    ],
    "tests/test_user.py": [
        "test_register", "test_register_duplicate", "test_login", "test_login_wrong_password",
        "test_profile_update", "test_password_hash",
    ],
    "tests/test_inventory.py": [
        "test_stock_decrement", "test_stock_restore_on_cancel", "test_low_stock_warning",
        "test_concurrent_reserve",
    ],
    "tests/test_api.py": [
        "test_health", "test_list_products", "test_get_product_404", "test_create_order_schema",
        "test_rate_limit", "test_pagination",
    ],
}

_FLAKY = {
    "tests/test_checkout.py::test_checkout_payment_retry",
    "tests/test_checkout.py::test_checkout_idempotent",
    "tests/test_inventory.py::test_concurrent_reserve",
}

_BROKEN = {
    "tests/test_pricing.py::test_coupon_unknown",
    "tests/test_cart.py::test_remove_missing_item",
}


def _fake_failure(seed: int, nodeid: str, tmpdir: str) -> str:
    """造一段形似 pytest 的失败输出。

    环境噪声（临时目录、数字、行号）随 seed 变化，异常类型与消息骨架保持不变——
    这正是失败指纹要处理的场景：同一个 bug，每次报错的数字和路径都不同。
    """
    file_part, _, name = nodeid.partition("::")
    name = name or nodeid
    line = 10 + seed % 40
    expected = 100 + seed % 7
    got = 900 + seed
    return (
        f"___________________________ {name} ___________________________\n\n"
        f"tmp_path = {tmpdir}/{name}{seed}\n\n"
        f"    def {name}():\n"
        f">       assert resp.status_code == {expected}\n"
        f"E       assert {got} == {expected}\n\n"
        f"{file_part}:{line}: AssertionError"
    )


def cmd_seed_demo(args) -> int:
    """写入一份合成数据，方便在没有任何历史时演示看板。项目名固定为 demo-shop。"""
    rng = random.Random(args.seed)
    project = "demo-shop"
    now = utcnow()
    commits = [f"{rng.randrange(16 ** 7):07x}" for _ in range(args.runs)]

    with _store(args) as store:
        for i in range(args.runs):
            started = now - timedelta(days=args.runs - i, hours=rng.uniform(0, 5))
            cases: list[CaseResult] = []
            for file_part, names in _DEMO_SUITE.items():
                for name in names:
                    nodeid = f"{file_part}::{name}"
                    fast = name.startswith(("test_health", "test_login"))
                    duration = round(rng.uniform(0.002, 0.02) if fast else rng.uniform(0.05, 1.4), 4)
                    outcome = OUTCOME_PASSED
                    raw = ""

                    if nodeid in _BROKEN:
                        outcome = OUTCOME_FAILED
                        raw = _fake_failure(i * 7 + len(name), nodeid, f"/tmp/pytest-of-runner/pytest-{i}")
                        duration = round(rng.uniform(0.03, 0.2), 4)
                    elif nodeid in _FLAKY and rng.random() < 0.35:
                        outcome = OUTCOME_ERROR
                        raw = (
                            f"___________________________ {name} ___________________________\n\n"
                            f"E       TimeoutError: gateway did not respond in {800 + i * 13}ms\n\n"
                            f"{file_part}:{30 + i % 10}: TimeoutError"
                        )
                    elif nodeid.endswith("test_rate_limit") and rng.random() < 0.2:
                        outcome = OUTCOME_FAILED
                        raw = (
                            "___________________________ test_rate_limit ___________________________\n\n"
                            "E       AssertionError: expected 429, got 200\n\n"
                            f"{file_part}:44: AssertionError"
                        )
                    elif nodeid.endswith(("test_pagination", "test_max_quantity")):
                        outcome = OUTCOME_SKIPPED

                    fp = label = ""
                    if outcome in (OUTCOME_FAILED, OUTCOME_ERROR):
                        sig = signature(raw)
                        fp, label = sig.fingerprint, sig.label
                    cases.append(
                        CaseResult(
                            nodeid=nodeid,
                            outcome=outcome,
                            duration=duration,
                            message=label,
                            longrepr=raw,
                            fingerprint=fp,
                            signature=label,
                        )
                    )

            if i == 0:
                cases.insert(
                    0,
                    CaseResult(
                        nodeid="tests/test_api.py::test_create_order_schema",
                        outcome=OUTCOME_FAILED,
                        duration=0.41,
                        longrepr="___________________________ test_create_order_schema ___________________________\n\n"
                        "E       AssertionError: missing key 'total' in response\n\n"
                        "tests/test_api.py:58: AssertionError",
                        fingerprint=signature(
                            "tests/test_api.py:58: AssertionError\nE       AssertionError: missing key 'total' in response"
                        ).fingerprint,
                        message="AssertionError @ test_api.py — AssertionError: missing key <str> in response",
                    ),
                )

            run = RunInfo(
                project=project,
                started_at=started,
                finished_at=started,
                duration=round(sum(c.duration for c in cases) + rng.uniform(1.5, 4.0), 3),
                git_commit=commits[i],
                git_branch="main",
                ci_build=str(1000 + i),
                env=env_fingerprint(),
                command=f"pytest --lens (demo run {i + 1})",
            )
            store.record_run(run, cases)

    print(f"已写入 {args.runs} 次演示运行，项目 {project} -> {args.db or DEFAULT_DB}")
    print("启动看板： lens serve" + (f" --db {args.db}" if args.db else ""))
    return 0


# ----------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lens", description="pytest 测试结果看板")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_db(p):
        p.add_argument("--db", default=None, help=f"SQLite 路径，默认 {DEFAULT_DB}")

    p = sub.add_parser("import", help="导入 JUnit XML")
    p.add_argument("files", nargs="+", help="XML 文件或通配符")
    p.add_argument("--project", default=None, help="项目名，默认取文件名")
    p.add_argument("--commit", default=None)
    p.add_argument("--branch", default=None)
    add_db(p)
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("stats", help="终端里看汇总")
    p.add_argument("--project", default=None)
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--min-runs", type=int, default=3)
    add_db(p)
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("report", help="生成 Markdown 报告")
    p.add_argument("--project", default=None)
    p.add_argument("--out", default=None, help="输出文件，默认打印到终端")
    p.add_argument("--trend", type=int, default=15)
    p.add_argument("--top", type=int, default=10)
    add_db(p)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("serve", help="启动 Web 看板")
    p.add_argument("--project", default=None)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    add_db(p)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("seed-demo", help="写入演示数据（项目名 demo-shop）")
    p.add_argument("--runs", type=int, default=14)
    p.add_argument("--seed", type=int, default=7)
    add_db(p)
    p.set_defaults(func=cmd_seed_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
