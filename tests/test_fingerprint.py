"""失败指纹的核心承诺：环境变了、数据变了、行号变了，同一个 bug 仍是同一个指纹。"""

import pytest

from lens.fingerprint import (
    FailureSignature,
    exception_of,
    message_of,
    normalize,
    origin_of,
    signature,
)


def test_normalize_strips_environment_noise():
    raw = (
        "<Cart object at 0x7f9c1a2b3c4d> at /tmp/pytest-of-runner/pytest-8/test-31\n"
        "cart_id = 'a1b2c3d4-1111-2222-3333-444455556666'\n"
        "created = 2026-09-12T04:31:07\n"
        "value = 12345\n"
    )
    out = normalize(raw)
    assert "0x7f9c1a2b3c4d" not in out
    assert "pytest-of-runner" not in out
    assert "a1b2c3d4-1111" not in out
    assert "2026-09-12" not in out
    assert "12345" not in out
    assert "<object>" in out and "<tmp>" in out


def test_normalize_collapses_whitespace_and_blank_lines():
    assert normalize("a   b\n\n\n   c  \n") == "a b\nc"


def test_normalize_collapses_numbers_glued_to_a_unit():
    """数字紧挨着单位字母时最容易被漏掉，而那恰恰是最常见的「每次都不同」。"""
    assert normalize("gateway did not respond in 813ms") == "gateway did not respond in Nms"
    assert normalize("took 1.5s") == "took Ns"
    assert normalize("memory 12.5 MB") == "memory N MB"


def test_normalize_keeps_digits_that_are_part_of_an_identifier():
    assert normalize("test_total0") == "test_total0"
    assert normalize("response utf8") == "response utf8"


def test_timeouts_differing_only_in_elapsed_time_share_a_fingerprint():
    a = "E       TimeoutError: gateway did not respond in 813ms\n\ntests/test_checkout.py:34: TimeoutError"
    b = "E       TimeoutError: gateway did not respond in 826ms\n\ntests/test_checkout.py:35: TimeoutError"
    assert signature(a).fingerprint == signature(b).fingerprint


def test_points_differing_only_in_price_share_a_fingerprint():
    a = "E       AssertionError: expected 19.99, got 24.50\n\ntests/test_pricing.py:8: AssertionError"
    b = "E       AssertionError: expected 29.99, got 34.50\n\ntests/test_pricing.py:9: AssertionError"
    assert signature(a).fingerprint == signature(b).fingerprint


def test_normalize_none_and_empty():
    assert normalize(None) == ""
    assert normalize("") == ""


def _assertion_failure(tmpdir, value, line):
    return (
        "___________________________ test_total ___________________________\n\n"
        f"tmp_path = {tmpdir}/test_total0\n\n"
        "    def test_total():\n"
        f">       assert {value} == 200\n"
        f"E       assert {value} == 200\n\n"
        f"tests/test_cart.py:{line}: AssertionError"
    )


def test_same_bug_in_different_environments_shares_fingerprint():
    a = _assertion_failure("/tmp/pytest-of-runner/pytest-8", 405, 12)
    b = _assertion_failure("/tmp/pytest-of-alice/pytest-3", 412, 47)
    assert signature(a).fingerprint == signature(b).fingerprint


def test_different_exception_gives_different_fingerprint():
    a = _assertion_failure("/tmp/x/pytest-1", 405, 12)
    b = (
        "    def test_total():\n"
        ">       raise KeyError('sku')\n"
        "E       KeyError: 'sku'\n\n"
        "tests/test_cart.py:12: KeyError"
    )
    assert signature(a).fingerprint != signature(b).fingerprint


def test_different_origin_file_gives_different_fingerprint():
    a = signature("E       KeyError: 'sku'\n\ntests/test_cart.py:3: KeyError")
    b = signature("E       KeyError: 'sku'\n\ntests/test_pricing.py:9: KeyError")
    assert a.origin == "test_cart.py"
    assert b.origin == "test_pricing.py"
    assert a.fingerprint != b.fingerprint


@pytest.mark.parametrize(
    "text,expected",
    [
        ("tests/test_a.py:5: AssertionError", "AssertionError"),
        ("tests/test_a.py:5: builtins.ValueError", "ValueError"),
        ("pytest.TimeoutError: gateway timeout", "TimeoutError"),
        ("no exception here at all", ""),
    ],
)
def test_exception_extraction(text, expected):
    assert exception_of(text) == expected


def test_message_prefers_pytest_e_line():
    text = (
        "___________________________ test_x ___________________________\n\n"
        "tmp_path = /tmp/whatever\n\n"
        "    def test_x():\n"
        ">       assert 1 == 2\n"
        "E       assert 1 == 2\n\n"
        "tests/test_a.py:3: AssertionError"
    )
    assert message_of(text) == "assert N == N"


def test_origin_ignores_line_numbers():
    assert origin_of("tests/deep/test_a.py:120: AssertionError") == "test_a.py"
    assert origin_of("no path here") == ""


def test_signature_label_is_human_readable():
    sig = signature("E       AssertionError: missing key 'total'\n\ntests/test_api.py:58: AssertionError")
    assert sig.exception == "AssertionError"
    assert sig.origin == "test_api.py"
    assert "AssertionError" in sig.label and "test_api.py" in sig.label


def test_signature_is_stable_and_short():
    sig = signature("E       ValueError: boom\n\ntests/test_a.py:1: ValueError")
    assert len(sig.fingerprint) == 12
    again = signature("E       ValueError: boom\n\ntests/test_a.py:1: ValueError")
    assert sig.fingerprint == again.fingerprint


def test_signature_tolerates_empty_input():
    sig = signature(None)
    assert len(sig.fingerprint) == 12
    assert sig.exception == ""


def test_normalize_handles_crlf_the_same_way_as_lf():
    assert normalize("a\r\nb") == normalize("a\nb") == "a\nb"


def test_normalize_erases_windows_absolute_paths():
    out = normalize(r"File 'C:\Users\bob\project\tests\test_a.py'")
    assert "C:" not in out
    assert "bob" not in out


def test_normalize_keeps_the_structure_of_a_multiline_traceback():
    text = "line one\n\n\nline two   with    spaces"
    assert normalize(text) == "line one\nline two with spaces"


def test_message_of_falls_back_to_the_first_meaningful_line():
    assert message_of("boom happened\nsecond line") == "boom happened"
    assert message_of(None) == ""
    assert message_of("\n\n   \n") == ""


def test_message_of_truncates_long_messages():
    assert len(message_of("E " + "x" * 500, limit=20)) == 20


def test_exception_of_only_looks_at_the_last_line():
    text = "ValueError: outer\n\ntests/test_a.py:3: KeyError"
    assert exception_of(text) == "KeyError"
    # 最后一行没有 `文件:行号: 异常` 结构时，不再往上找，只做兜底扫描
    assert exception_of("just a ValueError somewhere") == "ValueError"


def test_origin_of_handles_windows_style_paths():
    assert origin_of(r"C:\repo\tests\test_cart.py:44: AssertionError") == "test_cart.py"


def test_signature_falls_back_to_the_message_argument():
    sig = signature(None, "E       ValueError: boom\n\ntests/test_a.py:1: ValueError")
    assert sig.exception == "ValueError"
    assert sig.origin == "test_a.py"
    assert sig.fingerprint == signature("E       ValueError: boom\n\ntests/test_a.py:1: ValueError").fingerprint


def test_failure_signature_label_is_unknown_when_nothing_is_known():
    sig = FailureSignature(fingerprint="0" * 12, exception="", origin="", message="")
    assert sig.label == "unknown failure"


def test_failure_signature_as_dict_exposes_the_label():
    data = signature("E       ValueError: boom\n\ntests/test_a.py:1: ValueError").as_dict()
    assert set(data) == {"fingerprint", "exception", "origin", "message", "label"}
    assert data["label"] == "ValueError @ test_a.py — ValueError: boom"
