"""菜单 → 接口 对照表的防漂移测试。

三件事必须永远成立：

1. `docs/erp-menu.md` 与 `erp menu --write` 的生成结果**逐字节一致**（改了能力清单忘了重新生成 → 失败）；
2. 每个能力声明的 `docs/api/<接口名>.md` 都真实存在；
3. 能力清单里的 **MCP 工具名 / CLI 工具名 / 页面路径** 都能在代码与菜单树里对上
   （防止改名后文档静默失效）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from erp_tools import menu_catalog as mc
from erp_tools.core.menu import menu_pages, parse_menu_tree, tree_to_items
from erp_tools.tools.registry import tool_names

ROOT = Path(__file__).resolve().parents[2]  # 仓库根（monorepo：api/ + web/）
API_ROOT = ROOT / "api"
MENU_TREE = ROOT / mc.MENU_TREE_JSON
MENU_DOC = ROOT / mc.MENU_DOC
MCP_SOURCE = API_ROOT / "src" / "erp_tools" / "apps" / "mcp" / "tools.py"


def all_capabilities():
    for caps in mc.PAGE_CAPABILITIES.values():
        yield from caps
    yield from mc.INFRA_CAPABILITIES


def load_meta_tree():
    data = json.loads(MENU_TREE.read_text(encoding="utf-8"))
    return data, tree_to_items(data.get("tree"))


def mcp_tool_names() -> set[str]:
    src = MCP_SOURCE.read_text(encoding="utf-8")
    return set(re.findall(r'name="(erp_\w+)"', src))


# ---------- 1. 生成物一致 ----------
def test_generated_doc_is_up_to_date() -> None:
    meta, tree = load_meta_tree()
    expected = mc.render_markdown(tree, meta)
    actual = MENU_DOC.read_text(encoding="utf-8")
    assert actual == expected, "docs/erp-menu.md 已过期，跑 `erp menu --write` 重新生成"


# ---------- 2. 文档存在 ----------
@pytest.mark.parametrize("cap", list(all_capabilities()), ids=lambda c: c.title)
def test_capability_has_doc(cap: mc.Capability) -> None:
    assert cap.doc, f"{cap.title} 没有绑定文档"
    assert (ROOT / "docs" / cap.doc).is_file(), f"找不到 docs/{cap.doc}"


def test_no_orphan_api_docs() -> None:
    """docs/api/ 里不能有没被能力清单引用的孤儿文档。"""
    referenced = {(ROOT / "docs" / cap.doc).resolve() for cap in all_capabilities()}
    orphans = [
        p.name for p in (ROOT / "docs" / mc.API_DIR).glob("*.md")
        if p.resolve() not in referenced
    ]
    assert not orphans, f"这些文档没有被 erp-menu.md 引用：{orphans}"


# ---------- 3. 名字对得上 ----------
def test_mcp_names_exist() -> None:
    known = mcp_tool_names()
    declared = {cap.mcp for cap in all_capabilities() if cap.mcp}
    missing = declared - known
    assert not missing, f"能力清单里的 MCP 工具名在 apps/mcp/tools.py 里不存在：{missing}"


def test_every_mcp_tool_is_documented() -> None:
    declared = {cap.mcp for cap in all_capabilities() if cap.mcp}
    undocumented = mcp_tool_names() - declared
    assert not undocumented, (
        f"这些 MCP 工具没有出现在菜单对照表里：{undocumented}；"
        "请在 menu_catalog.PAGE_CAPABILITIES / INFRA_CAPABILITIES 里登记"
    )


def test_cli_names_exist() -> None:
    known = set(tool_names())
    for cap in all_capabilities():
        if cap.cli:
            assert cap.cli in known, f"未知 CLI 工具 {cap.cli!r}，可用：{sorted(known)}"


def test_page_paths_exist_in_menu_tree() -> None:
    _meta, tree = load_meta_tree()
    paths = {item.page_path for _p, item in menu_pages(tree)}
    unknown = set(mc.PAGE_CAPABILITIES) - paths
    assert not unknown, f"能力清单里的页面路径不在菜单树里（拼错或已改版）：{unknown}"


def test_doc_name_matches_mcp_tool() -> None:
    """文档文件名必须就是接口名，避免 `erp_xxx.md` 写成别的。"""
    for cap in all_capabilities():
        if cap.doc and cap.mcp:
            assert cap.doc == f"{mc.API_DIR}/{cap.mcp}.md", (
                f"{cap.mcp} 的文档路径应写成 {mc.API_DIR}/{cap.mcp}.md，实际 {cap.doc}"
            )


# ---------- 菜单树本身 ----------
def test_menu_tree_fixture_still_parses() -> None:
    fixture = API_ROOT / "tests" / "fixtures" / "menu_tree.js"
    tree = parse_menu_tree(fixture.read_text(encoding="utf-8"))
    assert len(tree) == 10
