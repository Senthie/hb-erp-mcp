"""客户报价【列表】页（BaoJia/BJ_KHBJ/Index.aspx）解析。

与 BJ_KHBJXQ 列表页是同一套模板，所以解析逻辑直接复用 `tools/gridlist.py`。
本页的差异（实测 2026-11，QXID=860）：

  * 一行 = **一个报价产品行**（56 列），不是一张单据；同一表单编号会出现多行
    （表单编号形如 `KHBJ261112003-01`，末尾 `-NN` 是行号）。
  * 列：选择/信息/打印 + 报价状态、有排交期、客户名称、香港单号、产品类型、
    装订方式、产品类别、产品名称、预计报完日期、规格、款数、语言数、
    最小值/中间值/最大值、单位、总P数、客户目标价、单价、各类单价、
    金额、纸张费用、辅料金额、印前金额、印刷金额、印后金额、版费、纸箱费、
    台版费、海运费、其他金额、业务员、制单日期、制单人、审核人、审核日期、
    备注、表单编号、签订单、存放位置、参数对比、来源单号、订单编号、需求产品备注3。
  * 末尾有一行「合计」（`lblRowsCount` 条），由通用解析器的 合计 标记丢掉。
  * 主记录 ID 在 `ondblclick="OnDBClickOpenData('<ID>','860',3)"`。
"""

from __future__ import annotations

from ..gridlist import GridPage, parse_grid_page

PAGE_PATH = "BaoJia/BJ_KHBJ/Index.aspx"
#: 详情页（按记录 ID 打开，Option=3 只读）
ADD_PAGE_PATH = "BaoJia/BJ_KHBJ/Add.aspx"
#: 客户报价列表的默认权限号（实测 2026-11，账号 2069 的菜单）
DEFAULT_QXID = "860"
#: 详情页的默认权限号
ADD_QXID = "857"

#: 兼容 gridlist 的命名
PriceListPage = GridPage


def parse_price_list_page(html: str) -> GridPage:
    """解析一次客户报价列表查询结果。

    不按列名裁剪：本页每一行都是「产品行」，没有必须存在的单号列
    （空占位行由通用解析器通过「全空」判定丢弃）。
    """
    return parse_grid_page(html)
