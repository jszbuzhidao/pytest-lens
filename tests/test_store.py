"""存储层：写入、聚合查询，以及「flaky 判定只有一套实现」这个约定。"""

import pytest

from lens.fingerprint import signature
from lens.models import CaseResult, RunInfo
from lens.store import Store

CART_FAIL = (
    "___________________________ test_total ___________________________\n\n"
    "E       AssertionError: expected 3, got 7\n\n"
    "tests/test_cart.py:12: AssertionError"
)
CART_FAIL_OTHER_RUN = (
    "___________________________ test_total ___________________________\n\n"
    "E       AssertionError: expected 5, got 9\n\n"
    "tests/test_cart.py:31: AssertionError"
)
TIMEOUT_FAIL = (
    "E       TimeoutError: gateway did not respond in 800ms\n\n"
    "tests/test_checkout.py:20: TimeoutError"
)


def failed_case(nodeid, raw, duration=0.1, outcome="failed"):
    sig = signature(raw)
    return CaseResult(
        nodeid=nodeid,
        outcome=outcome,
        duration=duration,
        message=sig.label,
        longrepr=raw,
        fingerprint=sig.fingerprint,
        signature=sig.label,
    )


def passed_case(nodeid, duration=0.05):
    return CaseResult(nodeid=nodeid, outcome="passed", duration=duration)


def skipped_case(nodeid):
    return CaseResult(nodeid=nodeid, outcome="skipped")


def add_run(store, cases, project="proj", commit="abc1234", duration=1.0):
    run = RunInfo(
        project=project,
        duration=duration,
        git_commit=commit,
        git_branch="main",
        ci_build="42",
        env="py3.13",
        command="pytest",
    )
    return store.record_run(run, cases)


# ------------------------------------------------------------------ 写入与基础


def test_record_run_returns_increasing_ids_and_persists_cases(store):
    first = add_run(store, [passed_case("tests/test_a.py::test_x")])
    second = add_run(store, [passed_case("tests/test_a.py::test_y")])
    assert second == first + 1
    detail = store.run_detail(first)
    assert [c["nodeid"] for c in detail["cases"]] == ["tests/test_a.py::test_x"]


def test_record_run_recomputes_tally_from_cases(store):
    run_id = add_run(
        store,
        [passed_case("t.py::a"), skipped_case("t.py::b")],
    )
    detail = store.run_detail(run_id)
    assert detail["run"]["total"] == 2
    assert detail["run"]["passed"] == 1
    assert detail["run"]["skipped"] == 1


def test_runs_pass_rate_excludes_skipped(store):
    add_run(store, [passed_case("t.py::a"), skipped_case("t.py::b"), skipped_case("t.py::c")])
    assert store.runs("proj")[0]["pass_rate"] == pytest.approx(1.0)


def test_runs_can_be_filtered_by_project_and_limited(store):
    for i in range(3):
        add_run(store, [passed_case(f"t.py::test_{i}")], project="alpha")
    add_run(store, [passed_case("t.py::other")], project="beta")
    assert len(store.runs("alpha", limit=99)) == 3
    assert len(store.runs("alpha", limit=2)) == 2
    assert len(store.runs()) == 4


def test_run_detail_unknown_id_raises_keyerror(store):
    with pytest.raises(KeyError):
        store.run_detail(9999)


def test_run_detail_sorts_errors_first_then_failures(store):
    run_id = add_run(
        store,
        [
            passed_case("t.py::ok", duration=9.0),
            failed_case("t.py::bad", CART_FAIL, duration=0.1),
            failed_case("t.py::boom", TIMEOUT_FAIL, duration=0.1, outcome="error"),
        ],
    )
    outcomes = [c["outcome"] for c in store.run_detail(run_id)["cases"]]
    assert outcomes == ["error", "failed", "passed"]


# -------------------------------------------------------------------- projects


def test_projects_lists_each_project_once_with_run_count(store):
    add_run(store, [passed_case("t.py::a")], project="alpha")
    add_run(store, [passed_case("t.py::b")], project="alpha")
    add_run(store, [passed_case("t.py::c")], project="beta")
    projects = {p["project"]: p["runs"] for p in store.projects()}
    assert projects == {"alpha": 2, "beta": 1}


def test_projects_is_empty_on_a_fresh_database(store):
    assert store.projects() == []


# --------------------------------------------------------------------- summary


def test_summary_without_any_run_is_empty_but_well_formed(store):
    summary = store.summary("proj")
    assert summary["latest"] is None
    assert summary["previous"] is None
    assert summary["delta"] == {}


def test_summary_on_first_run_has_no_delta(store):
    add_run(store, [passed_case("t.py::a")])
    summary = store.summary("proj")
    assert summary["latest"]["passed"] == 1
    assert summary["previous"] is None
    assert summary["delta"] == {}


def test_summary_reports_delta_against_previous_run(store):
    add_run(store, [passed_case("t.py::a"), failed_case("t.py::b", CART_FAIL)])
    add_run(store, [passed_case("t.py::a"), passed_case("t.py::b")])
    delta = store.summary("proj")["delta"]
    assert delta["failed"] == -1
    assert delta["passed"] == 1
    assert delta["pass_rate"] == pytest.approx(0.5)


def test_summary_counts_flaky_cases(store):
    for outcomes in (["passed"], ["failed"], ["passed"]):
        raw = CART_FAIL if outcomes == ["failed"] else ""
        case = failed_case("t.py::wobble", raw) if raw else passed_case("t.py::wobble")
        add_run(store, [case])
    assert store.summary("proj")["flaky"] == 1


# ----------------------------------------------------------------------- trend


def test_trend_is_chronological_not_reverse_chronological(store):
    for i in range(3):
        add_run(store, [passed_case(f"t.py::{i}")])
    ids = [r["id"] for r in store.trend("proj")]
    assert ids == sorted(ids)


# --------------------------------------------------------------------- slowest


def test_slowest_skips_skipped_cases_and_sorts_desc(store):
    add_run(
        store,
        [
            passed_case("t.py::fast", duration=0.01),
            passed_case("t.py::slow", duration=5.0),
            skipped_case("t.py::never"),
        ],
    )
    slow = store.slowest("proj", limit=10)
    assert [c["nodeid"] for c in slow] == ["t.py::slow", "t.py::fast"]


def test_slowest_only_looks_at_the_requested_run_window(store):
    add_run(store, [passed_case("t.py::old", duration=99.0)])
    add_run(store, [passed_case("t.py::new", duration=0.2)])
    assert [c["nodeid"] for c in store.slowest("proj", last_runs=1)] == ["t.py::new"]
    assert len(store.slowest("proj", last_runs=2)) == 2


# ------------------------------------------------------------ failure clusters


def test_same_bug_across_runs_lands_in_one_cluster(store):
    add_run(store, [failed_case("tests/test_cart.py::test_total", CART_FAIL)])
    add_run(store, [failed_case("tests/test_cart.py::test_total", CART_FAIL_OTHER_RUN)])
    clusters = store.failure_clusters("proj")
    assert len(clusters) == 1
    assert clusters[0]["occurrences"] == 2
    assert clusters[0]["cases"] == 1


def test_distinct_bugs_land_in_distinct_clusters(store):
    add_run(store, [failed_case("t.py::a", CART_FAIL), failed_case("t.py::b", TIMEOUT_FAIL)])
    assert {c["fingerprint"] for c in store.failure_clusters("proj")} == {
        signature(CART_FAIL).fingerprint,
        signature(TIMEOUT_FAIL).fingerprint,
    }


def test_cluster_lists_sample_nodeids_and_is_capped(store):
    cases = [failed_case(f"t.py::test_{i}", CART_FAIL) for i in range(8)]
    add_run(store, cases)
    cluster = store.failure_clusters("proj")[0]
    assert cluster["cases"] == 8
    assert len(cluster["sample_cases"]) == 5
    assert cluster["message"]


def test_cluster_ignores_pass_and_skipped_results(store):
    add_run(store, [passed_case("t.py::ok"), skipped_case("t.py::never")])
    assert store.failure_clusters("proj") == []


def test_cluster_window_limits_how_far_back_it_looks(store):
    add_run(store, [failed_case("t.py::a", CART_FAIL)])
    add_run(store, [passed_case("t.py::a")])
    assert store.failure_clusters("proj", last_runs=1) == []
    assert len(store.failure_clusters("proj", last_runs=2)) == 1


# ----------------------------------------------------------------------- flaky


def test_flaky_detects_alternating_outcomes(store):
    add_run(store, [passed_case("t.py::wobble")])
    add_run(store, [failed_case("t.py::wobble", CART_FAIL)])
    add_run(store, [passed_case("t.py::wobble")])
    flaky = store.flaky("proj", min_runs=3)
    assert len(flaky) == 1
    assert flaky[0]["nodeid"] == "t.py::wobble"
    assert flaky[0]["passes"] == 2 and flaky[0]["fails"] == 1


def test_flaky_ignores_stable_and_insufficient_cases(store):
    add_run(store, [passed_case("t.py::stable"), passed_case("t.py::few")])
    add_run(store, [passed_case("t.py::stable"), failed_case("t.py::few", CART_FAIL)])
    assert store.flaky("proj", min_runs=3) == []


def test_flaky_respects_min_runs_and_limit(store):
    for i in range(4):
        case = passed_case("t.py::wobble") if i % 2 == 0 else failed_case("t.py::wobble", CART_FAIL)
        add_run(store, [case])
    assert store.flaky("proj", min_runs=5) == []
    assert len(store.flaky("proj", min_runs=2, limit=1)) == 1


# -------------------------------------------------------------- case_history


def test_case_history_reports_one_entry_per_run_newest_first(store):
    add_run(store, [passed_case("t.py::x")])
    add_run(store, [failed_case("t.py::x", CART_FAIL)])
    history = store.case_history("t.py::x")
    assert [h["outcome"] for h in history] == ["failed", "passed"]
    assert {h["project"] for h in history} == {"proj"}


def test_case_history_is_empty_for_unknown_nodeid(store):
    add_run(store, [passed_case("t.py::x")])
    assert store.case_history("t.py::nope") == []


# ------------------------------------------------------------------------ prune


def test_prune_keeps_only_the_most_recent_runs(store):
    for i in range(6):
        add_run(store, [passed_case(f"t.py::{i}")])
    removed = store.prune("proj", keep=2)
    assert removed == 4
    assert [r["id"] for r in store.runs("proj", limit=99)] == [6, 5]


def test_prune_does_not_touch_other_projects(store):
    for i in range(3):
        add_run(store, [passed_case(f"t.py::{i}")], project="alpha")
    add_run(store, [passed_case("t.py::b")], project="beta")
    store.prune("alpha", keep=1)
    assert len(store.runs("beta", limit=99)) == 1


# -------------------------------------------------------------------- plumbing


def test_store_creates_parent_directory(tmp_path):
    db = tmp_path / "nested" / "deep" / "lens.db"
    with Store(db):
        pass
    assert db.exists()


def test_store_accepts_in_memory_database(tmp_path):
    store = Store(":memory:")
    try:
        add_run(store, [passed_case("t.py::a")])
        assert store.runs("proj")[0]["passed"] == 1
    finally:
        store.close()


def test_store_is_a_context_manager(tmp_path):
    with Store(tmp_path / "lens.db") as store:
        assert isinstance(store, Store)
    with pytest.raises(Exception):
        store.conn.execute("SELECT 1")
