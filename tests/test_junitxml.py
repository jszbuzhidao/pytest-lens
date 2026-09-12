"""JUnit XML 导入：CI 上只留下一份 xml 时，历史不能断。"""

import textwrap

import pytest

from lens.junitxml import build_nodeid, parse_junit

SINGLE_SUITE = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="pytest" tests="4" failures="1" errors="1" skipped="1" time="1.25">
  <testcase classname="tests.test_api" name="test_ok" time="0.01"/>
  <testcase classname="tests.test_api" name="test_bad" time="0.02">
    <failure message="AssertionError: expected 200">E       AssertionError: expected 200

tests/test_api.py:12: AssertionError</failure>
  </testcase>
  <testcase classname="tests.test_cart" name="test_boom" time="0.03">
    <error message="TimeoutError: gateway">E       TimeoutError: gateway

tests/test_cart.py:7: TimeoutError</error>
  </testcase>
  <testcase classname="tests.test_api" name="test_skip" time="0.0">
    <skipped message="pending"/>
  </testcase>
</testsuite>
"""

MULTI_SUITE = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="unit">
    <testcase classname="tests.test_a" name="test_one" time="0.5"/>
  </testsuite>
  <testsuite name="integration">
    <testcase classname="tests.test_b" name="test_two" time="0.25"/>
  </testsuite>
</testsuites>
"""

EMPTY_ROOT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites/>"""

WITH_FILE_ATTR = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="pytest">
  <testcase file="tests/test_deep.py" classname="TestCart" name="test_add" time="0.1"/>
</testsuite>
"""


def write(tmp_path, name, xml):
    path = tmp_path / name
    path.write_text(textwrap.dedent(xml), encoding="utf-8")
    return path


# ------------------------------------------------------------------ build_nodeid


@pytest.mark.parametrize(
    "classname,name,expected",
    [
        ("tests.test_api", "test_ok", "tests/test_api.py::test_ok"),
        ("tests.test_api.TestCart", "test_add", "tests/test_api.py::TestCart::test_add"),
        ("TestCart", "test_add", "TestCart::test_add"),
        ("", "test_bare", "test_bare"),
        ("tests", "test_nested", "tests.py::test_nested"),
    ],
)
def test_build_nodeid_reconstructs_pytest_style(classname, name, expected):
    assert build_nodeid(classname, name) == expected


def test_build_nodeid_prefers_the_file_attribute():
    assert build_nodeid("TestCart", "test_add", "tests/test_deep.py") == (
        "tests/test_deep.py::TestCart::test_add"
    )


def test_build_nodeid_with_file_attribute_but_no_classname():
    assert build_nodeid("", "test_add", "tests/test_deep.py") == "tests/test_deep.py::test_add"


# ------------------------------------------------------------------- parse_junit


def test_parse_single_suite_counts_outcomes(tmp_path):
    run, cases = parse_junit(write(tmp_path, "junit.xml", SINGLE_SUITE))
    assert run.total == 4
    assert (run.passed, run.failed, run.errored, run.skipped) == (1, 1, 1, 1)
    assert run.duration == pytest.approx(0.06)
    assert run.command == "import junit.xml"


def test_parse_marks_skipped_element_as_skipped_not_failed(tmp_path):
    _, cases = parse_junit(write(tmp_path, "junit.xml", SINGLE_SUITE))
    by_name = {c.name: c for c in cases}
    assert by_name["test_skip"].outcome == "skipped"
    assert by_name["test_ok"].outcome == "passed"


def test_parse_attaches_fingerprint_to_failures_only(tmp_path):
    _, cases = parse_junit(write(tmp_path, "junit.xml", SINGLE_SUITE))
    by_name = {c.name: c for c in cases}
    assert by_name["test_bad"].fingerprint
    assert by_name["test_boom"].fingerprint
    assert by_name["test_ok"].fingerprint == ""
    assert by_name["test_skip"].fingerprint == ""


def test_parse_keeps_message_and_body_of_a_failure(tmp_path):
    _, cases = parse_junit(write(tmp_path, "junit.xml", SINGLE_SUITE))
    bad = next(c for c in cases if c.name == "test_bad")
    assert "AssertionError" in bad.longrepr
    assert "test_api.py:12" in bad.longrepr
    assert bad.message


def test_parse_multi_suite_document(tmp_path):
    run, cases = parse_junit(write(tmp_path, "junit.xml", MULTI_SUITE))
    assert run.total == 2
    assert {c.name for c in cases} == {"test_one", "test_two"}


def test_parse_document_without_any_suite_yields_nothing(tmp_path):
    run, cases = parse_junit(write(tmp_path, "junit.xml", EMPTY_ROOT))
    assert cases == []
    assert run.total == 0


def test_parse_honours_the_file_attribute(tmp_path):
    _, cases = parse_junit(write(tmp_path, "junit.xml", WITH_FILE_ATTR))
    assert cases[0].nodeid == "tests/test_deep.py::TestCart::test_add"


def test_parse_deduplicates_repeated_nodeids(tmp_path):
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <testsuites>
      <testsuite name="a"><testcase classname="tests.test_a" name="test_one" time="1"/></testsuite>
      <testsuite name="b"><testcase classname="tests.test_a" name="test_one" time="1"/></testsuite>
    </testsuites>
    """
    run, cases = parse_junit(write(tmp_path, "junit.xml", xml))
    assert len(cases) == 1
    assert run.total == 1


def test_parse_survives_garbage_time_attribute(tmp_path):
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <testsuite name="pytest">
      <testcase classname="tests.test_a" name="test_one" time="not-a-number"/>
    </testsuite>
    """
    run, cases = parse_junit(write(tmp_path, "junit.xml", xml))
    assert cases[0].duration == 0.0
    assert run.duration == 0.0


def test_parse_uses_filename_as_default_project(tmp_path):
    run, _ = parse_junit(write(tmp_path, "report-42.xml", SINGLE_SUITE))
    assert run.project == "report-42"


def test_parse_failure_without_a_body_still_gets_a_message(tmp_path):
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <testsuite name="pytest">
      <testcase classname="tests.test_a" name="test_one" time="0.1">
        <failure message="ValueError: bad input"/>
      </testcase>
    </testsuite>
    """
    _, cases = parse_junit(write(tmp_path, "junit.xml", xml))
    assert cases[0].outcome == "failed"
    assert cases[0].fingerprint
    assert "bad input" in cases[0].message


def test_parse_looks_past_non_result_children_like_system_out(tmp_path):
    """testcase 下面可能先有 system-out 之类的子节点，别把结果判成「通过」。"""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <testsuite name="pytest">
      <testcase classname="tests.test_a" name="test_one" time="0.1">
        <system-out>some captured stdout</system-out>
        <failure message="AssertionError: nope">E       AssertionError: nope

tests/test_a.py:4: AssertionError</failure>
      </testcase>
    </testsuite>
    """
    _, cases = parse_junit(write(tmp_path, "junit.xml", xml))
    assert cases[0].outcome == "failed"
    assert cases[0].fingerprint
