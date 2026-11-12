"""产品类别树：解析 + 解析器（让「新产品没有档案」也能建明细）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from erp_tools.core.errors import ParseError
from erp_tools.tools.quote_create import (
    CategoryOption,
    ProductOption,
    QuoteCreateService,
    clear_options_cache,
    parse_category_tree,
)

LINK_HTML = """
<td><a class="TreeView1_0" href="javascript:OnTreeView1Click(&#39;27.01;9e132616-cb3c-4400-bb3a-af32ec2f87e2&#39;)" id="TreeView1t41">普通BB书</a></td>
<td><a class="TreeView1_0" href="javascript:OnTreeView1Click(&#39;29.04;64baf656-1d5b-4eab-b22e-9d73edad77f8&#39;)" id="TreeView1t42">其它套装</a></td>
<td><a class="TreeView1_0" href="javascript:OnTreeView1Click(&#39;22.01;b5554438-669d-4674-83a0-06866d32c9bc&#39;)" id="TreeView1t43">圆脊精装书</a></td>
"""

STORAGE_HTML = """
<script>
ComponentArt_Storage_TreeView1 = [['p0',-1,[1],[['Value','9e132616-cb3c-4400-bb3a-af32ec2f87e2'],['Text','27.01;普通BB书']]],['p1',0,[],[['Value','64baf656-1d5b-4eab-b22e-9d73edad77f8'],['Text','29.04;其它套装']]]];
</script>
"""


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_options_cache()
    yield
    clear_options_cache()


# ══════════════════ 解析 ══════════════════
def test_parse_category_tree_from_links() -> None:
    cats = parse_category_tree(LINK_HTML)

    assert [c.code for c in cats] == ["27.01", "29.04", "22.01"]
    first = cats[0]
    assert first.lbid == "9e132616-cb3c-4400-bb3a-af32ec2f87e2"
    assert first.name == "普通BB书"
    assert first.text == "27.01;普通BB书"


def test_parse_category_tree_from_js_storage() -> None:
    cats = parse_category_tree(STORAGE_HTML)

    assert [c.code for c in cats] == ["27.01", "29.04"]
    assert cats[1].name == "其它套装"


def test_parse_category_tree_dedupes_same_guid() -> None:
    """页面上链接与 JS 存储都在时，同一 GUID 只能出现一次。"""
    cats = parse_category_tree(LINK_HTML + STORAGE_HTML)

    assert len(cats) == 3
    assert len({c.lbid for c in cats}) == 3


def test_parse_category_tree_ignores_empty() -> None:
    assert parse_category_tree("") == []
    assert parse_category_tree("<html>没有树</html>") == []


# ══════════════════ 解析成唯一节点 ══════════════════
class _StubService(QuoteCreateService):
    def __init__(self, categories: list[CategoryOption]) -> None:
        super().__init__(auth=SimpleNamespace(cfg=SimpleNamespace(username="tester")))  # type: ignore[arg-type]
        self._cats = categories

    def load_categories(self, *, refresh: bool = False):  # noqa: D102
        return list(self._cats)


def _service() -> _StubService:
    return _StubService(parse_category_tree(LINK_HTML))


@pytest.mark.parametrize("keyword", [
    "27.01",                       # 编码
    "27.01;普通BB书",               # 编码;名称
    "普通BB书",                    # 名称
    "9e132616-cb3c-4400-bb3a-af32ec2f87e2",   # GUID
])
def test_find_category_accepts_every_form(keyword: str) -> None:
    cat = _service().find_category(keyword)
    assert cat.code == "27.01"
    assert cat.lbid == "9e132616-cb3c-4400-bb3a-af32ec2f87e2"


def test_find_category_unique_partial_match() -> None:
    assert _service().find_category("套装").code == "29.04"


def test_find_category_unknown_raises() -> None:
    with pytest.raises(ParseError) as exc:
        _service().find_category("不存在的类别")
    assert "找不到" in str(exc.value)


def test_find_category_ambiguous_raises() -> None:
    """「书」在三条里都出现 → 必须报「匹配到 N 个」而不是瞎猜一个。"""
    with pytest.raises(ParseError) as exc:
        _service().find_category("书")
    assert "匹配到" in str(exc.value)


# ══════════════════ 把类别补回提交体 ══════════════════
#: `SelectCPZL(...)` 的位置参数：0=CPID 1=CPBH 2=CPMC 3=LBID 4=LBBM 5=CPLB
_ARCHIVE_ARGS = (
    "d089da3e-091e-44d9-80e9-b7690cf2cc6a",      # txtCPID
    "04.01.0935",                                 # txtCPBH
    "512961503  INDIVIDUAL  BOX,SO15631(B),STD",  # txtCPMC
    "facb71ed-f8c5-4d0a-baea-80b8cc2cb094",      # txtLBID
    "04.01",                                      # txtLBBM
    "STD彩盒",                                    # txtCPLB（只有名称！）
)


def _body() -> dict[str, str]:
    return {"txtCPLB_Input": "<--请选择-->", "txtLBBM": "", "txtLBID": ""}


def test_restore_category_joins_code_and_name_when_tree_misses() -> None:
    """档案只给名称（`STD彩盒`），必须拼成 ERP 认的 `04.01;STD彩盒`。

    类别树是按产品大类过滤的（书版树里没有 04.x 彩盒），所以这条回退路径很常用：
    之前只写名称能过校验但**存不住**，列表「产品类别」一直是空的。
    """
    svc = _StubService([])                 # 空树 → 走拼接回退
    archive = ProductOption(args=_ARCHIVE_ARGS)
    body = _body()

    svc._restore_product_category(body, "<html></html>", archive.name, archive=archive)

    assert body["txtCPLB_Input"] == "04.01;STD彩盒"
    assert body["txtLBBM"] == "04.01"
    assert body["txtLBID"] == "facb71ed-f8c5-4d0a-baea-80b8cc2cb094"


def test_restore_category_uses_tree_when_available() -> None:
    """树里能按 LBID 命中时，用树上的规范文本（名称可能和档案不一致）。"""
    cats = parse_category_tree(STORE_HTML_FOR_LBID)
    svc = _StubService(cats)
    archive = ProductOption(args=_ARCHIVE_ARGS)
    body = _body()

    svc._restore_product_category(body, "<html></html>", archive.name, archive=archive)

    assert body["txtCPLB_Input"] == "04.01;彩盒"     # 以树为准
    assert body["txtLBBM"] == "04.01"


def test_restore_category_skips_when_page_already_has_value() -> None:
    svc = _StubService([])
    archive = ProductOption(args=_ARCHIVE_ARGS)
    body = _body()
    html = '<input name="txtCPLB_Input" value="27.01;普通BB书" />'

    svc._restore_product_category(body, html, archive.name, archive=archive)

    assert body["txtCPLB_Input"] == "<--请选择-->"    # 不动已有值
    assert body["txtLBBM"] == ""


STORE_HTML_FOR_LBID = """
<script>
ComponentArt_Storage_TreeView1 = [['p0',-1,[],[['Value','facb71ed-f8c5-4d0a-baea-80b8cc2cb094'],['Text','04.01;彩盒']]]];
</script>
"""
