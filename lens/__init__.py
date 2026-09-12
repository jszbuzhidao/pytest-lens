"""pytest-lens：pytest 结果聚合、失败聚类与 flaky 检测。"""

from .fingerprint import FailureSignature, normalize, signature
from .flaky import FlakyVerdict, classify, classify_all, only_flaky
from .models import CaseResult, RunInfo, split_nodeid
from .store import Store

__version__ = "0.1.0"
__all__ = [
    "CaseResult",
    "FailureSignature",
    "FlakyVerdict",
    "RunInfo",
    "Store",
    "__version__",
    "classify",
    "classify_all",
    "normalize",
    "only_flaky",
    "signature",
    "split_nodeid",
]
