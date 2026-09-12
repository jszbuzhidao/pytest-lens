"""失败指纹：把「同一个 bug」的多次失败归并成一类。

不同机器、不同临时目录、不同行号、不同随机数据导致的同一个失败，
应该落到同一个 fingerprint —— 这是失败聚类的全部意义。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_OBJECT_REPR = re.compile(r"<[^>\n]{1,60} object at 0x[0-9a-fA-F]+>")
_HEX = re.compile(r"0x[0-9a-fA-F]{3,}")
_TMP_DIR = re.compile(r"[^\s'\"]*pytest-of-[^\s'\"/\\]+[^\s'\"]*")
_WIN_PATH = re.compile(r"[A-Za-z]:\\(?:[^\\\s:;,'\"()\[\]]+\\)*")
_POSIX_PATH = re.compile(r"/(?:[^/\s:;,'\"()\[\]]+/)+")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_ISO_TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_STRING = re.compile(r"'[^'\n]{0,200}'|\"[^\"\n]{0,200}\"")
# 不能写成 \b\d+\b：数字紧挨着单位字母时（"813ms"、"1.5s"）两边都没有 \b，
# 数字就漏掉了，「同一个 bug」会被拆成每个运行一个指纹。
# 只用前视排除「前一个字符像标识符」的情况，好让 "test_total0"、"utf8" 保持原样。
_NUMBER = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?")
_SPACES = re.compile(r"[ \t]+")


def normalize(text: str | None) -> str:
    """把一段失败文本归一化成可比较的形式。

    依次抹掉：对象地址、十六进制、临时目录、绝对路径、UUID、时间戳、
    引号内容、数字，最后压缩空白。保留结构，丢掉「每次都不一样」的部分。
    """
    if not text:
        return ""
    out = text.replace("\r\n", "\n")
    out = _OBJECT_REPR.sub("<object>", out)
    out = _ISO_TS.sub("<ts>", out)
    out = _UUID.sub("<uuid>", out)
    out = _HEX.sub("0xADDR", out)
    out = _TMP_DIR.sub("<tmp>", out)
    out = _WIN_PATH.sub("<dir>/", out)
    out = _POSIX_PATH.sub("<dir>/", out)
    out = _STRING.sub("<str>", out)
    out = _NUMBER.sub("N", out)
    lines = [_SPACES.sub(" ", ln).strip() for ln in out.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _lines(text: str | None) -> list[str]:
    return [ln.strip() for ln in (text or "").replace("\r\n", "\n").split("\n")]


def exception_of(text: str | None) -> str:
    """取异常类名。pytest 的 longrepr 末行形如 `tests/test_a.py:12: ValueError`。"""
    for line in reversed(_lines(text)):
        if not line:
            continue
        m = re.search(r":\s*([A-Za-z_][\w.]*)\s*$", line)
        if m:
            return m.group(1).split(".")[-1]
        break
    m = re.search(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Failure|Warning|Exit))\b", text or "")
    return m.group(1) if m else ""


def origin_of(text: str | None) -> str:
    """崩溃点所在文件的 basename（丢掉行号，行号会变）。"""
    for line in reversed(_lines(text)):
        if not line:
            continue
        m = re.search(r"([^\\/:]+\.py):\d+", line)
        if m:
            return m.group(1)
        break
    return ""


def message_of(text: str | None, limit: int = 160) -> str:
    """断言/异常的具体信息，优先取 pytest 的 `E ` 行。"""
    e_lines = [ln[2:].strip() for ln in _lines(text) if ln.startswith("E ")]
    candidate = e_lines[0] if e_lines else next((ln for ln in _lines(text) if ln), "")
    return normalize(candidate)[:limit]


@dataclass(frozen=True)
class FailureSignature:
    fingerprint: str
    exception: str
    origin: str
    message: str

    @property
    def label(self) -> str:
        bits = [b for b in (self.exception, self.origin) if b]
        head = " @ ".join(bits) or "unknown failure"
        return f"{head} — {self.message}" if self.message else head

    def as_dict(self) -> dict:
        return {
            "fingerprint": self.fingerprint,
            "exception": self.exception,
            "origin": self.origin,
            "message": self.message,
            "label": self.label,
        }


def signature(longrepr: str | None, message: str | None = None) -> FailureSignature:
    """计算失败指纹。同样的错误在不同环境/行号/数据下应得到同一个指纹。"""
    text = longrepr or message or ""
    exc = exception_of(text)
    origin = origin_of(text)
    msg = message_of(text) or normalize(message or "")[:160]
    material = "\n".join((exc, origin, msg))
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:12]
    return FailureSignature(fingerprint=digest, exception=exc, origin=origin, message=msg)
