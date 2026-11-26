"""夹具加载：真实抓取的 ERP 页面，用于解析器回归测试。

只要解析器改动，就跑这几个测试——不必再去跑全量 18496 条才能发现「页面结构又变了」。
"""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_text():
    def _read(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    return _read
