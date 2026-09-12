"""JUnit XML 导入。

CI 上跑完测试通常只留下一份 junit.xml。没有这个入口，看板就只能看到
「本地开着 --lens 跑过的那几次」，历史是断的。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .fingerprint import signature
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

_FAILED_TAGS = {"failure": OUTCOME_FAILED, "error": OUTCOME_ERROR, "skipped": OUTCOME_SKIPPED}


def build_nodeid(classname: str, name: str, file_attr: str | None = None) -> str:
    """把 JUnit 的 classname/name 还原成 pytest 风格的 nodeid。"""
    if file_attr:
        return f"{file_attr}::{classname}::{name}" if classname else f"{file_attr}::{name}"
    if not classname:
        return name
    parts = [p for p in classname.split(".") if p]
    cls = ""
    if parts and parts[-1][:1].isupper():
        cls = parts.pop()
    module = "/".join(parts) + ".py" if parts else ""
    if cls:
        return f"{module}::{cls}::{name}" if module else f"{cls}::{name}"
    return f"{module}::{name}" if module else name


def _longrepr(node: ET.Element) -> str:
    text = (node.text or "").strip()
    message = (node.get("message") or "").strip()
    if message and text:
        return f"{message}\n{text}"
    return message or text


def parse_junit(path: str | Path) -> tuple[RunInfo, list[CaseResult]]:
    """解析一份 JUnit XML，返回 (RunInfo, cases)。RunInfo 的统计会被重算。"""
    path = Path(path)
    root = ET.parse(str(path)).getroot()

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if root.tag == "testsuites" and not suites:
        suites = [root]

    cases: list[CaseResult] = []
    seen: set[str] = set()
    total_time = 0.0
    for suite in suites:
        for tc in suite.iter("testcase"):
            nodeid = build_nodeid(tc.get("classname", ""), tc.get("name", ""), tc.get("file"))
            if nodeid in seen:
                continue
            seen.add(nodeid)

            outcome = OUTCOME_PASSED
            raw = ""
            for child in tc:
                if child.tag in _FAILED_TAGS:
                    outcome = _FAILED_TAGS[child.tag]
                    raw = _longrepr(child)
                    break

            try:
                duration = float(tc.get("time") or 0.0)
            except (TypeError, ValueError):
                duration = 0.0
            total_time += duration

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
                    longrepr=raw[:4000],
                    fingerprint=fp,
                    signature=label,
                )
            )

    run = RunInfo(
        project=path.stem,
        started_at=utcnow(),
        finished_at=utcnow(),
        duration=total_time,
        env=env_fingerprint(),
        command=f"import {path.name}",
    )
    return run.tally(cases), cases
