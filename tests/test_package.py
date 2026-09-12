"""包级别的承诺：公开 API 都在，版本号没漂。"""

import lens


def test_version_is_pinned():
    assert lens.__version__ == "0.1.0"


def test_every_name_in_all_is_importable():
    missing = [name for name in lens.__all__ if not hasattr(lens, name)]
    assert missing == []


def test_documented_shortcuts_are_the_real_objects():
    from lens.fingerprint import signature
    from lens.store import Store

    assert lens.signature is signature
    assert lens.Store is Store
