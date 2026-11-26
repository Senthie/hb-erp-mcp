"""三个模块的页面规格（实测 2026-11-18，账号 15627 / UserID 2069）。

QXID 一律允许配置覆盖（`ERP_QXID_*`），探测顺序：配置 > 菜单 > 内置默认值。
内置默认值来自该账号的 `GetMenuTree` 实测结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModuleSpec:
    """一个列表模块的页面与筛选规格。"""

    #: 模块键（CLI/MCP 参数里用）
    key: str
    #: 中文名
    label: str
    #: 列表页路径（相对 /Admin/）
    list_path: str
    #: 详情页路径（相对 /Admin/）；空表示该页没有独立详情（用内联网格）
    detail_path: str
    #: 菜单里探测不到时的兜底 QXID
    fallback_qxid: str
    #: 配置覆盖用的环境变量名
    qxid_env: str
    #: 一行代表什么（写进返回里，避免误读）
    row_note: str
    #: 日期区间作用于哪个字段的 `<select>`（空 = 该页固定按制单日期）
    date_control: str = ""
    #: 日期字段：中文名 -> 提交值
    date_fields: dict[str, str] = field(default_factory=dict)
    #: 服务端过滤下拉：控件名 -> 中文说明
    filter_controls: dict[str, str] = field(default_factory=dict)
    #: 过滤别名：中文键 -> 控件名（含控件名本身）
    filter_aliases: dict[str, str] = field(default_factory=dict)
    #: 记录 ID 之外的「单号」列名（用于按单号反查）
    number_column: str = ""
    #: 子数据节点：node -> (子页文件名, 打开它的按钮, 中文说明)
    nodes: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    #: 详情页里「子数据」节点的中文别名（node -> 别名列表）
    node_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: 实测在 ERP 侧已失效的节点（按钮指向 404 页面）
    dead_nodes: frozenset[str] = frozenset()

    def control_for(self, key: str) -> str:
        """把用户写的过滤键（别名或控件名）解析成控件名。"""
        key = (key or "").strip()
        if key in self.filter_controls:
            return key
        return self.filter_aliases.get(key, "")

    def resolve_date_field(self, name: str) -> str:
        """日期字段中文名/代码 -> 提交值；不认的直接当提交值。"""
        name = (name or "").strip()
        if not name:
            return ""
        if name in self.date_fields:
            return self.date_fields[name]
        if name in self.date_fields.values():
            return name
        return name

    def default_date_field(self) -> str:
        return next(iter(self.date_fields.values()), "")


# ══════════════════ 生产施工单 ══════════════════
PRODUCTION = ModuleSpec(
    key="production",
    label="生产施工单",
    list_path="ShengChan/SC_SCSGD/Index.aspx",
    detail_path="ShengChan/SC_SCSGD/Add.aspx",
    fallback_qxid="740",
    qxid_env="ERP_QXID_PRODUCTION_INDEX",
    row_note="一行 = 一张生产施工单（生产单号 SGD 开头）",
    date_control="txtRQLX",
    date_fields={"制单日期": "0", "交货日期": "1", "审批日期": "2"},
    filter_controls={
        "txtSCZT": "工单状态",
        "txtCheck": "审批情况",
        "txt_SFBF": "报废状态",
        "dropGDLX": "工单类型",
        "txtWJZT2": "文件状态",
        "txtTSZT": "推送状态",
        "txtBSC": "是否补数",
        "txtDBF": "是否调版",
        "txtBGF": "是否变更",
        "txtKHSX": "客户属性",
        "txtMBF": "模板",
        "txtZDWF": "是否自动跟进",
        "txtSFDY": "是否打印",
        "dropSFZDGJ": "是否自动跟进单",
        "txtDataLX": "数据类型",
        "txtXGYY": "修改原因",
        "txtBetRQ": "预计报价月份",
        "txtFXCX": "查询方式",
    },
    filter_aliases={
        "工单状态": "txtSCZT", "状态": "txtSCZT", "生产状态": "txtSCZT",
        "审批": "txtCheck", "审批情况": "txtCheck",
        "报废": "txt_SFBF", "报废状态": "txt_SFBF",
        "工单类型": "dropGDLX", "类型": "dropGDLX",
        "文件状态": "txtWJZT2",
        "推送状态": "txtTSZT",
        "是否补数": "txtBSC", "补数": "txtBSC",
        "是否调版": "txtDBF",
        "是否变更": "txtBGF", "变更": "txtBGF",
        "客户属性": "txtKHSX",
        "模板": "txtMBF",
        "是否打印": "txtSFDY",
        "数据类型": "txtDataLX",
        "修改原因": "txtXGYY",
        "查询方式": "txtFXCX",
    },
    number_column="生产单号",
    nodes={
        # node -> (子页, 按钮, 说明)   —— 实测 2026-11-18，账号 15627
        "cp": ("Add_CPZL.aspx", "btnSelectCPZL", "产品资料表单（整张产品页）"),
        "cp_process": ("Add_CPGXZL.aspx", "btnSelectCPGXZL", "产品级工序"),
        "cp_materials": ("Add_CPFL.aspx", "btnSelectCPFL", "产品级辅料"),
        "part": ("Add_BJZL.aspx", "btnSelectBJZL", "部件印刷资料"),
        "part_new": ("Add_BJZL.aspx", "btnNewBJGXZL", "新增部件表单"),
        "part_materials": ("Add_BJFL.aspx", "btnSelectBJFL", "部件已选辅料"),
        "part_process": ("Add_BJGXZL.aspx", "btnSelectBJGXZL", "部件工序"),
        "part_machines": ("Add_BJJTZL.aspx", "btnSelectBJJTZL", "部件机台"),
        "product_check": ("Add_CPSHZL.aspx", "btnSelectCPSHZL", "送货/审核资料"),
        "part_pick": ("Modify_CPSHZL_Select.aspx", "btnBJMS", "部件明细（选择部件）"),
        "product_bg": ("Add_CPZLBG.aspx", "btnSelectCPZLBG", "产品资料变更记录"),
        "feedback": ("Add_SCSGDXQFK.aspx", "btnSelectKHBJXQFK", "生产需求反馈正文"),
        "sgkb": ("Add_SGKB.aspx", "BtnOpenSGKBT", "手工开版资料附件"),
        "template_product": ("Add_CPMXZL.aspx", "btnSelectCPMXForm", "产品模板（模板产品）"),
        "by_template": ("Add_ByMFMB.aspx", "btnSelectMBCPZL", "按模板新建产品"),
        "binding": ("Add_D1_D1_D10.aspx", "btnSelectBJZL_D10", "装订/贴数(D10)明细"),
        "package_content": ("Add_D1_D9.aspx", "btnSelectBXNR", "包装内容(D9)明细"),
        "stock": ("Base_CPSJKC_Select.aspx", "btn_SJKCMX", "生产库存明细（选择页）"),
        # 注：`Add_QTFX.aspx`(其他费用) / `Brow_FXMX.aspx`(费项) 只在报价页渲染按钮，
        # 施工单页里 `btnSelectQTFX`/`btnBrowFXMX` **根本不存在** → 不作为节点声明。
    },
    node_aliases={
        "产品资料": "cp", "产品": "cp", "产品表单": "cp",
        "产品工序": "cp_process", "成品工序": "cp_process",
        "产品辅料": "cp_materials",
        "部件": "part", "部件资料": "part",
        "新增部件": "part_new",
        "部件辅料": "part_materials", "辅料": "part_materials",
        "部件工序": "part_process",
        "部件机台": "part_machines", "机台": "part_machines",
        "送货资料": "product_check", "送货信息": "product_check", "审核资料": "product_check",
        "部件明细": "part_pick", "部件选择": "part_pick",
        "产品变更": "product_bg", "变更记录": "product_bg",
        "需求反馈": "feedback", "报价反馈": "feedback",
        "开版": "sgkb", "手工开版": "sgkb",
        "模板产品": "template_product", "产品模板": "template_product",
        "按模板": "by_template",
        "装订": "binding", "贴数": "binding", "D10": "binding",
        "包装内容": "package_content",
        "库存": "stock", "生产库存": "stock",
    },
)

# ══════════════════ 采购订单 ══════════════════
PURCHASE = ModuleSpec(
    key="purchase",
    label="采购订单",
    list_path="CaiGou/CG_CGDD/Index.aspx",
    detail_path="CaiGou/CG_CGDD/Add.aspx",
    fallback_qxid="266",
    qxid_env="ERP_QXID_PURCHASE_INDEX",
    row_note="一行 = 一条采购订单材料明细（表单编号 CGDD 开头，一单多料号）",
    date_control="",
    date_fields={"制单日期": "ZDRQ"},
    filter_controls={
        "txtCheck": "审批情况",
        "txtBDZT": "表单状态",
        "txtSFMX": "数据粒度",
        "txtCLLB": "材料类别",
        "txtSFFSC": "是否分生产",
        "txtYGDD": "是否已过订单",
        "txtBZDD": "标准订单",
        "txtGHFS": "供货方式",
        "txtGXPQ": "工序排期",
        "txtSFRK": "入库情况",
        "txtSFBRK": "是否不入库",
        "txtSFDY": "是否打印",
        "txtFXCX": "查询方式",
    },
    filter_aliases={
        "审批": "txtCheck", "审批情况": "txtCheck",
        "表单状态": "txtBDZT", "状态": "txtBDZT",
        "数据粒度": "txtSFMX", "明细/汇总": "txtSFMX",
        "材料类别": "txtCLLB", "类别": "txtCLLB",
        "是否分生产": "txtSFFSC",
        "是否已过订单": "txtYGDD",
        "标准订单": "txtBZDD",
        "供货方式": "txtGHFS",
        "工序排期": "txtGXPQ",
        "入库": "txtSFRK", "入库情况": "txtSFRK",
        "是否不入库": "txtSFBRK",
        "是否打印": "txtSFDY",
        "查询方式": "txtFXCX",
    },
    number_column="表单编号",
    nodes={
        # 采购订单详情**没有**弹窗数据子页：材料明细是页面内联网格（gridView2）。
        # 这里保留的是「选择/编辑」类弹窗，方便确认「哪些页面有、哪些没有」。
        "supplier": ("Base_GYSZL_zixuan.aspx", "btn_SelectGYS", "供应商档案选择页（选择弹窗）"),
    },
    node_aliases={"供应商": "supplier", "供应商档案": "supplier"},
)

# ══════════════════ 客户订单 ══════════════════
#: 菜单：**客户订单管理 › 客户订单 › 列表-客户订单**
#: ⚠ `DD_KHDD/Index.aspx` 同路径还挂着「合同评审」QXID=1291，必须用 687。
SALES_ORDER = ModuleSpec(
    key="sales_order",
    label="客户订单",
    list_path="DingDan/DD_KHDD/Index.aspx",
    detail_path="DingDan/DD_KHDD/Add.aspx",
    fallback_qxid="687",
    qxid_env="ERP_QXID_SALES_ORDER_INDEX",
    row_note=("一行 = 一个订单产品行（表单编号 KHDD 开头，同一单号可能多行）；"
              "92 列，含订单状态/订单日期/客户单号/订单数量/价格金额等"),
    date_control="txtRQLX",
    date_fields={"制单日期": "ZDRQ", "订单日期": "DDRQ",
                 "离厂日期": "LCRQ", "交货日期": "JHRQ"},
    filter_controls={
        "txtDDZT": "订单状态",
        "txtCheck": "审批情况",
        "txt_SFBF": "报废状态",
        "txtDisplay": "数据类型",
        "txt_TZQK": "通知情况",
        "txtZDWF": "整单外发",
        "txtBSC": "是否生产",
        "txtXGJHRQ": "修改交期",
        "txtKHSX": "客户属性",
        "txtXZLDF": "资料袋",
        "txtCLZJXS": "材料直销",
        "txtWJZT": "文件批核状态",
        "txtXMZT": "箱唛批核状态",
        "txtKDQK": "开单情况",
        "txtSFHJ": "是否核价",
        "txtFKQX": "付款期限",
        "txtZDFS": "装订方式",
        "txtYGXM": "业务员",
        "txtCPSX": "产品属性",
        "txtBetRQ": "预计报价月份",
        "txtSFDY": "是否打印",
        "txtFXCX": "查询方式",
    },
    filter_aliases={
        "订单状态": "txtDDZT", "状态": "txtDDZT",
        "审批": "txtCheck", "审批情况": "txtCheck", "审核": "txtCheck",
        "报废": "txt_SFBF", "报废状态": "txt_SFBF",
        "数据类型": "txtDisplay", "产品/送货信息": "txtDisplay",
        "通知情况": "txt_TZQK", "通知": "txt_TZQK",
        "整单外发": "txtZDWF", "外发": "txtZDWF",
        "是否生产": "txtBSC", "生产": "txtBSC",
        "修改交期": "txtXGJHRQ", "交期确认": "txtXGJHRQ",
        "客户属性": "txtKHSX",
        "资料袋": "txtXZLDF",
        "材料直销": "txtCLZJXS",
        "文件状态": "txtWJZT", "文件批核": "txtWJZT",
        "箱唛状态": "txtXMZT", "箱唛": "txtXMZT",
        "开单情况": "txtKDQK", "开单": "txtKDQK",
        "是否核价": "txtSFHJ", "核价": "txtSFHJ",
        "付款期限": "txtFKQX", "收款期限": "txtFKQX",
        "装订方式": "txtZDFS", "装订": "txtZDFS",
        "业务员": "txtYGXM",
        "产品属性": "txtCPSX",
        "预计报价月份": "txtBetRQ",
        "是否打印": "txtSFDY",
        "查询方式": "txtFXCX",
    },
    number_column="表单编号",
    nodes={
        # 实测 2026-11-20（回发每个按钮抓到的真实子页）
        "cp": ("Add_CPZL.aspx", "btnSelectCPZL", "产品资料表单（整张产品页）"),
        "cp_process": ("Add_CPGXZL.aspx", "btnSelectCPGXZL", "产品级工序"),
        "cp_materials": ("Add_CPFL.aspx", "btnSelectCPFL", "产品级辅料"),
        "part": ("Add_BJZL.aspx", "btnSelectBJZL", "部件印刷资料"),
        "part_new": ("Add_BJZL.aspx", "btnNewBJGXZL", "新增部件表单"),
        "part_materials": ("Add_BJFL.aspx", "btnSelectBJFL", "部件已选辅料"),
        "part_process": ("Add_BJGXZL.aspx", "btnSelectBJGXZL", "部件工序"),
        "part_machines": ("Add_BJJTZL.aspx", "btnSelectBJJTZL", "部件机台"),
        "product_check": ("Add_CPSHZL.aspx", "btnSelectCPSHZL", "送货/审核资料"),
        "quantities": ("Add_CPSL.aspx", "btnSelectCPSLZL", "数量档（数量/单价/金额）"),
        "fees": ("Brow_FXMX.aspx", "btnBrowFXMX", "费项明细（每个数量档）"),
        "other_fees": ("Add_QTFX.aspx", "btnSelectQTFX", "其他费用项目"),
        "feedback": ("Add_KHDDXQFK.aspx", "btnSelectKHBJXQFK", "订单需求反馈正文"),
        "sgkb": ("Add_SGKB.aspx", "BtnOpenSGKBT", "手工开版资料附件"),
        "product_bg": ("Add_CPZLBG.aspx", "btnSelectCPZLBG", "产品资料变更记录"),
        "template_product": ("Add_CPMXZL.aspx", "btnSelectCPMXForm", "产品模板"),
        "by_template": ("Add_ByMFMB.aspx", "btnSelectMBCPZL", "按模板新建产品"),
        "product_guide": ("KHBJXQ_CPZHZY.aspx", "btnSelectCPZHZY", "产品综合指引"),
        "extra_info": ("KHBJXQ_CPGJXX.aspx", "btnSelectExport", "报价附加信息"),
        "stock": ("Base_CPSJKC_Select.aspx", "btn_SJKCMX", "生产库存明细（选择页）"),
    },
    node_aliases={
        "产品资料": "cp", "产品": "cp", "产品表单": "cp",
        "产品工序": "cp_process", "成品工序": "cp_process",
        "产品辅料": "cp_materials",
        "部件": "part", "部件资料": "part",
        "新增部件": "part_new",
        "部件辅料": "part_materials", "辅料": "part_materials",
        "部件工序": "part_process",
        "部件机台": "part_machines", "机台": "part_machines",
        "送货资料": "product_check", "送货信息": "product_check", "审核资料": "product_check",
        "数量": "quantities", "数量档": "quantities", "数量明细": "quantities",
        "费项": "fees", "费项明细": "fees", "成本明细": "fees",
        "其他费用": "other_fees", "其他费项": "other_fees",
        "需求反馈": "feedback", "订单需求": "feedback",
        "开版": "sgkb", "手工开版": "sgkb",
        "产品变更": "product_bg", "变更记录": "product_bg",
        "模板产品": "template_product", "产品模板": "template_product",
        "按模板": "by_template",
        "综合指引": "product_guide", "产品综合指引": "product_guide",
        "附加信息": "extra_info",
        "库存": "stock", "生产库存": "stock",
    },
    # 实测 2026-11-20：这两个按钮回发的弹窗指向 DD_KHDD 下不存在的 aspx（404）
    dead_nodes=frozenset({"fees", "other_fees"}),
)

# ══════════════════ 产品核价 ══════════════════
#: 与「客户报价」共用同一套页面（BJ_KHBJ），差别只在菜单 QXID=1281，
#: 因此只提供列表；详情/子数据直接复用 `erp_price_detail` / `erp_price_node`。
COSTING = ModuleSpec(
    key="costing",
    label="产品核价",
    list_path="BaoJia/BJ_KHBJ/Index.aspx",
    detail_path="BaoJia/BJ_KHBJ/Add.aspx",
    fallback_qxid="1281",
    qxid_env="ERP_QXID_COST_INDEX",
    row_note=("一行 = 一个核价产品行（表单编号 CPHJ 开头，同一单号可能多行 -01/-02）；"
              "详情与子数据用 erp_price_detail / erp_price_node"),
    date_control="txtZDorJH",
    date_fields={"制单日期": "ZDRQ", "交货日期": "KHJHRQ", "离厂日期": "LCRQ"},
    filter_controls={
        "txtCheck": "审批情况",
        "txt_SFBF": "报废状态",
        "txtQDQK": "确认情况",
        "txtXKHDD": "签订单",
        "txtKHSX": "客户属性",
        "txtMBF": "模板",
        "txtBetRQ": "预计报价月份",
        "txtCFWZ": "报价分类",
        "txtFXCX": "查询方式",
    },
    filter_aliases={
        "审批": "txtCheck", "审批状态": "txtCheck", "审批情况": "txtCheck",
        "报废": "txt_SFBF", "报废状态": "txt_SFBF",
        "确认": "txtQDQK", "确认情况": "txtQDQK",
        "签订单": "txtXKHDD", "合同": "txtXKHDD",
        "客户属性": "txtKHSX",
        "模板": "txtMBF",
        "预计报价月份": "txtBetRQ",
        "报价分类": "txtCFWZ",
        "查询方式": "txtFXCX",
    },
    number_column="表单编号",
)

MODULES: dict[str, ModuleSpec] = {
    spec.key: spec for spec in (PRODUCTION, PURCHASE, COSTING, SALES_ORDER)
}


def module_names() -> list[str]:
    return list(MODULES)


def get_module(key: str) -> ModuleSpec:
    from ...core.errors import ParseError

    name = (key or "").strip().lower()
    aliases = {
        "施工单": "production", "生产施工单": "production", "scsgd": "production",
        "采购": "purchase", "采购订单": "purchase", "cgdd": "purchase",
        "核价": "costing", "产品核价": "costing", "cphe": "costing", "cphj": "costing",
        "订单": "sales_order", "客户订单": "sales_order", "khdd": "sales_order",
        "销售订单": "sales_order", "sales_order": "sales_order",
    }
    name = aliases.get(name, name)
    if name not in MODULES:
        raise ParseError(f"未知模块 {key!r}；可用：{', '.join(MODULES)}")
    return MODULES[name]
