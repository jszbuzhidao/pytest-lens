"""把看板数据渲染成 Markdown 报告 —— 方便直接贴到 CI 日志或 PR 评论里。"""

from __future__ import annotations

from datetime import datetime, timezone

from .store import Store


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _delta(value: float | int | None, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:+.1f}{suffix}"
    return f"{value:+d}{suffix}"


def build_report(
    store: Store,
    project: str,
    trend_limit: int = 15,
    top: int = 10,
) -> str:
    summary = store.summary(project)
    latest = summary.get("latest")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out: list[str] = [f"# 测试质量报告 · {project}", "", f"生成时间：{now}", ""]

    if not latest:
        out.append("暂无运行记录。先执行 `pytest --lens` 或 `lens import <junit.xml>`。")
        return "\n".join(out)

    out += [
        "## 概览",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 最近运行 | #{latest['id']} @ {latest['started_at']} |",
        f"| 提交 | `{(latest['git_commit'] or '-')}` （{latest['git_branch'] or '-'}） |",
        f"| 用例总数 | {latest['total']} |",
        f"| 通过 / 失败 / 错误 / 跳过 | {latest['passed']} / {latest['failed']} / {latest['errored']} / {latest['skipped']} |",
        f"| 通过率 | **{_pct(latest['pass_rate'])}** |",
        f"| 用时 | {latest['duration']:.2f}s |",
        f"| 环境 | {latest['env']} |",
        "",
    ]

    if summary.get("previous"):
        d = summary["delta"]
        out += [
            f"与上一次相比：通过率 {_delta(d.get('pass_rate') * 100 if d.get('pass_rate') is not None else None, '%')}，"
            f"失败 {_delta(d.get('failed'))}，错误 {_delta(d.get('errored'))}，"
            f"用时 {_delta(d.get('duration'), 's')}",
            "",
        ]

    trend = store.trend(project, limit=trend_limit)
    if trend:
        out += ["## 通过率趋势", "", "| 运行 | 时间 | 总数 | 通过率 | 失败 | 用时(s) |", "| --- | --- | --- | --- | --- | --- |"]
        for r in trend:
            out.append(
                f"| #{r['id']} | {r['started_at']} | {r['total']} | {_pct(r['pass_rate'])} | "
                f"{r['failed'] + r['errored']} | {r['duration']:.2f} |"
            )
        out.append("")

    slowest = store.slowest(project, limit=top)
    if slowest:
        out += [f"## 最慢用例 TOP {len(slowest)}", "", "| 用例 | 耗时(s) | 结果 |", "| --- | --- | --- |"]
        for c in slowest:
            out.append(f"| `{c['nodeid']}` | {c['duration']:.3f} | {c['outcome']} |")
        out.append("")

    clusters = store.failure_clusters(project, limit=top)
    if clusters:
        out += ["## 失败聚类（同一指纹即同一个 bug）", "", "| 指纹 | 次数 | 涉及用例 | 最近出现 | 摘要 |", "| --- | --- | --- | --- | --- |"]
        for c in clusters:
            out.append(
                f"| `{c['fingerprint']}` | {c['occurrences']} | {c['cases']} | {c['last_seen']} | {c['message'][:60]} |"
            )
        out.append("")

    flaky = store.flaky(project, min_runs=2, limit=top)
    if flaky:
        out += ["## 不稳定用例（flaky）", "", "| 用例 | 出现次数 | 通过 | 失败 | flaky 指数 |", "| --- | --- | --- | --- | --- |"]
        for f in flaky:
            out.append(
                f"| `{f['nodeid']}` | {f['appearances']} | {f['passes']} | {f['fails']} | {f['flaky_score']:.2f} |"
            )
        out.append("")
    else:
        out += ["## 不稳定用例（flaky）", "", "最近窗口内没有发现时好时坏的用例。", ""]

    return "\n".join(out)
