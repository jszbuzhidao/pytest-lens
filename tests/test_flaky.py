"""flaky 判定：既通过过也失败过才算不稳定，跳过不算「跑过」。"""

from lens.flaky import (
    FLAKY,
    INSUFFICIENT,
    STABLE_FAIL,
    STABLE_PASS,
    classify,
    classify_all,
    only_flaky,
)


def test_single_failure_is_not_flaky_without_enough_history():
    v = classify("t.py::a", ["failed"], min_runs=3)
    assert v.verdict == INSUFFICIENT
    assert v.is_flaky is False


def test_alternating_result_is_flaky():
    v = classify("t.py::a", ["passed", "failed", "passed"], min_runs=3)
    assert v.verdict == FLAKY
    assert v.is_flaky is True
    assert v.passes == 2 and v.fails == 1


def test_flaky_score_is_failure_ratio():
    v = classify("t.py::a", ["passed", "passed", "failed", "failed"], min_runs=3)
    assert v.flaky_score == 0.5


def test_always_passing_is_stable_pass():
    v = classify("t.py::a", ["passed"] * 5, min_runs=3)
    assert v.verdict == STABLE_PASS
    assert v.flaky_score == 0.0


def test_always_failing_is_stable_fail_not_flaky():
    v = classify("t.py::a", ["failed", "error", "failed"], min_runs=3)
    assert v.verdict == STABLE_FAIL
    assert v.is_flaky is False


def test_skipped_is_not_counted_as_an_appearance():
    v = classify("t.py::a", ["passed", "failed", "skipped", "skipped"], min_runs=3)
    assert v.appearances == 2
    assert v.verdict == INSUFFICIENT


def test_classify_all_groups_history_and_puts_flaky_first():
    verdicts = classify_all(
        {
            "t.py::stable": ["passed"] * 4,
            "t.py::flaky": ["passed", "failed", "failed", "passed"],
            "t.py::broken": ["failed"] * 4,
        },
        min_runs=3,
    )
    assert [v.nodeid for v in verdicts][0] == "t.py::flaky"
    assert verdicts[0].verdict == FLAKY


def test_classify_all_accepts_flat_pairs():
    pairs = [("t.py::a", "passed"), ("t.py::a", "failed"), ("t.py::a", "passed")]
    verdicts = classify_all(pairs, min_runs=3)
    assert len(verdicts) == 1
    assert verdicts[0].verdict == FLAKY


def test_only_flaky_filters_the_rest_out():
    verdicts = classify_all(
        {"a": ["passed", "failed", "passed"], "b": ["passed"] * 3},
        min_runs=3,
    )
    assert [v.nodeid for v in only_flaky(verdicts)] == ["a"]


def test_as_dict_shape():
    data = classify("t.py::a", ["passed", "failed", "passed"], min_runs=3).as_dict()
    assert set(data) == {"nodeid", "appearances", "passes", "fails", "flaky_score", "verdict"}
