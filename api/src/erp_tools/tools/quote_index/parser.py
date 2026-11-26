"""客户报价需求【列表】页（BaoJia/BJ_KHBJXQ/Index.aspx）解析。

页面事实（实测 2026-11）：
  * 结果在 `div#gViewDiv > table#gridView`；
  * 第 1 行是 `tr.FixedTitleRow` 表头（38 列），数据行同样是 38 个 td，列序一一对应；
  * 每行的**主记录 ID** 在 `<tr ondblclick="OnDBClickOpenData('<主ID>','876',3)">` 里，
    `onclick="showMessagePop('<主ID>')"` 也带同一个 ID。
    注意行内还有别的 GUID（产品/客板子表 ID），不要按「第一个 GUID」取，必须认函数名。
  * 分页：`lblCurrentPage / lblPageCount / lblRowsCount`，翻页用 `__EVENTTARGET=btnNext`
    全量回发（本页可用，已实测）。

通用部分（表头 / 行对齐 / 记录 ID / 合计行）已抽到 `tools/gridlist.py`，
BaoJia 下同一套模板的页面（如 BJ_KHBJ 客户报价）共用同一份实现。
"""

from __future__ import annotations

from ..gridlist import (  # noqa: F401  （这些常量历史上从本模块导出）
    ACTION_COLUMNS,
    GRID_DIV_ID,
    ID_PATTERNS,
    TOTAL_ROW_MARKERS,
    GridPage,
    clean_header,
    extract_record_id,
    parse_grid_page,
)

#: 兼容旧名字
QuoteListPage = GridPage


def parse_list_page(html: str) -> GridPage:
    """解析一次列表查询结果（丢弃没有表单编号的行）。"""
    return parse_grid_page(html, require_columns=("表单编号",))
