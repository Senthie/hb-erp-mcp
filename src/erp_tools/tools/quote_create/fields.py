"""`Add_CPZL.aspx`（产品明细子页）**全量控件** ↔ JSON 字段名对照表。

来源：2026-11-12 用 `scripts/probe-quote-fields.py` 对真实页面做的 GET 抓取
（账号 21146 / user_id 2474，单据 `BJXQ261110003`），共 **205** 个控件，
其中可见 195 个（含 select / textarea / checkbox）。

设计原则（与 `service.prepare()` 一致）：

* **一个控件一条记录**，`control` 就是 ERP 提交字段名，永远是权威；
* `alias` 是给人用的 snake_case 名字，多个别名可指向同一控件；
* `visible` = **DOM 首屏可见**（祖先节点没有 `display:none`）；
* `writable` = ERP 侧接受写入（False = 只读 / disabled / 系统生成 / 计算值）；
* **只有 `visible and writable`（`uploadable`）的字段允许通过 MCP 上传**，
  折叠面板里的 142 个隐藏字段一律拒绝——它们不是「高级选项」，是别的分区的数据；
* `options` 只做**离线提示**（写库时仍以真实页面为准），用于在 `prepare` 阶段
  提前发现「填了一个已不存在的下拉值」。

不要在业务代码里硬编码这些名字，统一走 `CONTROL_MAP` / `ALIAS_MAP`；
要生成上传载荷的结构，用 `upload_payload_schema()`。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 控件种类
KIND_TEXT = "text"
KIND_NUMBER = "number"
KIND_DATE = "date"
KIND_SELECT = "select"
KIND_TEXTAREA = "textarea"
KIND_CHECKBOX = "checkbox"


@dataclass(frozen=True)
class FieldSpec:
    """一个明细子页控件的元数据。"""

    control: str
    label: str
    kind: str = KIND_TEXT
    #: 对外 JSON 字段名（空 = 只能按 control 名提交）
    alias: str = ""
    #: 额外的历史/口语化别名（如 `description`）
    extra_aliases: tuple[str, ...] = ()
    #: ERP 侧是否接受写入（False = 只读 / disabled / 系统生成 / 计算值）
    writable: bool = True
    #: ERP 保存时服务端强制要求的字段（缺了会被挡在「【XX】不能为空！」）
    required: bool = False
    #: 语义分组，仅用于文档与浏览
    group: str = ""
    #: 离线已知的下拉选项（显示文字 → 提交值）；空表示未知，写库时以页面为准
    options: dict[str, str] = field(default_factory=dict)
    note: str = ""

    @property
    def visible(self) -> bool:
        """**DOM 首屏可见**。折叠面板（`display:none`）里的字段一律 False。"""
        return self.control in VISIBLE_CONTROLS

    @property
    def uploadable(self) -> bool:
        """能否通过 MCP 上传。**可见 且 可写** 才行，两者缺一不可。"""
        return self.visible and self.writable


def _opt(*labels: str) -> dict[str, str]:
    """只记录显示文字（提交值在写库时按真实页面解析）。"""
    return {label: "" for label in labels}


_PLACEHOLDER = "<--请选择-->"

# ══════════════════ a. 产品标识 / 规格 ══════════════════
_IDENTITY: tuple[FieldSpec, ...] = (
    FieldSpec("txtCPID", "产品ID", group="标识", writable=False,
              note="产品档案 GUID，由 erp_quote_create 反查档案后自动填入"),
    FieldSpec("txtCPMC", "产品名称", alias="product_name", group="标识",
              note="顶层 name 用于检索产品档案；要覆盖写入的产品名称就用这个字段"),
    FieldSpec("txtDWMC", "单位", KIND_SELECT, "unit", group="标识",
              note="默认取产品档案的单位；要覆盖请传显示文字"),
    FieldSpec("txtCPBH", "产品编号", alias="product_code", group="标识",
              note="来自产品档案，可覆盖"),
    FieldSpec("txtKHCPBH", "客户产品编号", alias="customer_product_code", group="标识"),
    FieldSpec("txtKHCPMC", "客户单号", alias="customer_order_no", group="标识"),
    FieldSpec("txtECPMC", "产品英文名", alias="product_name_en", group="标识"),
    FieldSpec("txtCPDL", "产品大类", KIND_SELECT, "product_group", group="标识",
              options=_opt("书版"), note="来自产品档案"),
    FieldSpec("txtCPLB_Input", "产品类别（显示值）", alias="category", group="标识",
              writable=False, note="ComponentArt 树形下拉的前端显示值，ERP 不落库"),
    FieldSpec("txtLBID", "产品类别ID", alias="category_id", group="标识",
              note="与 txtLBBM 一起由档案回填，单发 txtCPLB_Input 无效"),
    FieldSpec("txtLBBM", "产品类别编码", alias="category_code", group="标识"),
    FieldSpec("txtGG", "规格（存档值）", alias="spec_text", group="标识",
              note="产品档案里的规格字符串；create() 目前仍以档案为准"),
    FieldSpec("txtCD", "长（MM）", KIND_NUMBER, "length_mm", group="规格"),
    FieldSpec("txtKD", "宽（MM）", KIND_NUMBER, "width_mm", group="规格"),
    FieldSpec("txtGD", "高（MM）", KIND_NUMBER, "height_mm", group="规格"),
    FieldSpec("txtGGLX", "规格描述（内径/外径）", KIND_SELECT, "spec_kind", group="规格",
              options=_opt("内径", "外径")),
    FieldSpec("Drop_SJFX", "书脊方向", KIND_SELECT, "spine_direction", group="规格",
              options=_opt("短书脊", "长书脊", "四边裁")),
    FieldSpec("txtCPYS", "产品样式（本式/卷式/折式/张式）", KIND_SELECT,
              "product_style", group="规格",
              options=_opt("本式", "卷式", "折式", "张式")),
    FieldSpec("txtCPSYTMC", "产品示意图", KIND_SELECT, "product_diagram", group="规格",
              note="选项随账号/客户变化，写库时以页面为准"),
    FieldSpec("txtCPZKCD", "产品展开长", KIND_NUMBER, "unfolded_length", group="规格"),
    FieldSpec("txtCPZKKD", "产品展开宽", KIND_NUMBER, "unfolded_width", group="规格"),
    FieldSpec("txtLYCD", "拉页长度", KIND_NUMBER, "gatefold_length", group="规格"),
    FieldSpec("txtSZB", "上折边", KIND_NUMBER, "fold_top", group="规格"),
    FieldSpec("txtXZB", "下折边", KIND_NUMBER, "fold_bottom", group="规格"),
    FieldSpec("txtCNB", "侧粘边", KIND_NUMBER, "glue_side", group="规格"),
    FieldSpec("txtDNB", "底粘边", KIND_NUMBER, "glue_bottom", group="规格"),
    FieldSpec("txtBQJBFX", "标签卷标方向", KIND_SELECT, "label_roll_direction",
              group="标签卷", options=_opt("向左", "向右")),
    FieldSpec("txtBQJNJ", "标签卷内径", KIND_SELECT, "label_roll_inner", group="标签卷"),
    FieldSpec("txtBQJWJ", "标签卷外径", KIND_NUMBER, "label_roll_outer", group="标签卷"),
    FieldSpec("txtMJZGS", "每卷/张个数", KIND_NUMBER, "per_roll_qty", group="标签卷"),
    FieldSpec("txtHBGP", "环保GP", KIND_SELECT, "eco_gp", group="标签卷"),
    FieldSpec("txtPXH", "排序号", KIND_NUMBER, "sort_no", group="标识"),
)

# ══════════════════ b. 装订 / 包装 / 物流 ══════════════════
_PACKAGING: tuple[FieldSpec, ...] = (
    FieldSpec("txtZDFS", "装订方式", KIND_SELECT, "binding", group="装订包装", required=True,
              options=_opt("胶装/PAD书", "精装书", "骑马钉", "其他", "套装", "车线书",
                           "线圈书", "快线BB书", "中线BB书", "慢线BB书")),
    FieldSpec("txtBZFS", "包装方式", KIND_SELECT, "packing", group="装订包装", required=True,
              options=_opt("纸箱包装", "牛皮纸包装", "胶袋包装", "卡板摆放")),
    FieldSpec("txtTBLX", "台板类型", KIND_SELECT, "pallet", group="装订包装", required=True,
              options=_opt(
                  "1.2m*0.8m(欧式)实木消毒卡板", "1.2*1.0m(欧式)实木消毒卡板",
                  "1.2m*0.8m(欧标)松木消毒卡板", "1.2*1.0松木消毒卡板(四边喷环保蓝漆)",
                  "1.165m*1.165m(欧式)实木消毒木卡板",
                  "1.2*1.0m实木消毒卡板(Highights专用）",
                  "1.2m*1.0m(欧标)松木消毒卡板（有盖板）", "不打卡板",
                  "1.0m*0.8m(欧式)实木消毒卡板")),
    FieldSpec("txtSHFS", "送货方式", KIND_SELECT, "delivery", group="装订包装", required=True,
              options=_opt(
                  "FOB(NGV)", "FOB 盐田", "送香港公司", "DPU", "直交工厂", "DAP",
                  "物流/速递", "工厂自提", "送国内", "FOB(美加线）-香港", "FOB蛇口",
                  "FOB(美加线）-盐田", "FOB 香港")),
    FieldSpec("txtYSLX", "运输类型", KIND_SELECT, "transport_type", group="装订包装",
              options=_opt("FOB盐田(普通)", "FOBHK", "FOB(拼柜)", "DPU", "CIF", "国内",
                           "FOB(底价)", "FOB蛇口", "FOB(美加线)")),
    FieldSpec("txtSHDQ", "送货地区", KIND_SELECT, "delivery_area", group="装订包装",
              options=_opt("FOB盐田(普通)", "香港", "FOB(美加线)", "FOB(NGV)", "DPU",
                           "日本（Tokyo）", "FOB蛇口"),
              note="服务端渲染时该下拉是空的（由【送货方式】级联生成），"
                   "纯 HTTP 提交实测不落库（2026-11-12 建单验证）"),
    FieldSpec("txtZXLB", "纸箱类别", KIND_SELECT, "carton_type", group="装订包装",
              options=_opt("普通纸箱", "半翼箱纸箱", "特殊纸箱", "天地盖纸箱")),
    FieldSpec("txtZBLX", "纸板类型", KIND_SELECT, "board_type", group="装订包装",
              options=_opt("特殊(看描述)", "A=B双坑纸板", "A3B单坑纸板", "A=A双坑纸板",
                           "K=K加强芯双坑箱", "K=K加强芯(耐破)")),
    FieldSpec("txtZXFS", "装箱方式", KIND_SELECT, "packing_orientation",
              group="装订包装", options=_opt("平装", "竖装")),
    FieldSpec("txtDKMC", "底卡名称", KIND_SELECT, "bottom_card", group="装订包装",
              options=_opt("B3B坑纸板", "A=B普通芯", "无需", "K=K加强芯", "卡纸", "特殊指定")),
    FieldSpec("chkBSYTB", "不使用台板", KIND_CHECKBOX, "no_pallet", group="装订包装"),
    FieldSpec("chkBSYJZX", "不使用集装箱", KIND_CHECKBOX, "no_container", group="装订包装"),
    FieldSpec("txtJHRQ", "交货日期", KIND_DATE, "delivery_date", group="交期", required=True),
    FieldSpec("txtWCRQ", "预计下单日期", KIND_DATE, "expected_date", group="交期", required=True),
    FieldSpec("txtLCRQ", "离厂日期", KIND_DATE, "factory_leave_date", group="交期"),
    FieldSpec("txtDZWJJQ", "电子文件交期", KIND_SELECT, "digital_file_due", group="交期"),
    FieldSpec("txtCYFS", "出样方式", KIND_SELECT, "sample_method", group="交期"),
    FieldSpec("txtZZJHXT", "最终交货形态", KIND_SELECT, "final_delivery_form", group="交期"),
    FieldSpec("txtZZJHXTMS", "最终交货形态描述", alias="final_delivery_form_desc",
              group="交期"),
    FieldSpec("txtYQWJKHQR", "印前文件客户确认方式", KIND_SELECT, "prepress_confirm_method",
              group="交期"),
    FieldSpec("chkZDBJF", "有排交期", KIND_CHECKBOX, "has_schedule", group="交期"),
)

# ══════════════════ c. 产品属性 / 类型 ══════════════════
_ATTRIBUTES: tuple[FieldSpec, ...] = (
    FieldSpec("txtCPSX", "产品属性", KIND_SELECT, "attribute", group="属性", required=True,
              options=_opt("三岁以上", "三岁以下", "其他", "3岁以下（要寄第三方测试）",
                           "3岁以上（要寄第三方测试）")),
    FieldSpec("txtCPLX", "产品类型", KIND_SELECT, "product_type", group="属性", required=True,
              options=_opt("新产品", "返单产品", "半返单旧产品", "半返单新产品", "特殊新产品")),
    FieldSpec("txtCPFDLX", "产品翻单类型", KIND_SELECT, "reorder_type", group="属性",
              options=_opt("新产品", "返单产品", "半返单旧产品", "半返单新产品", "特殊新产品")),
    FieldSpec("txtFSCMC", "FSC类别", KIND_SELECT, "fsc_category", group="属性",
              options=_opt("FSC Mix", "FSC 100%", "FSC Mix 85%", "FSC Mix Credit",
                           "FSC Recycled 85%", "FSC Recycled 100%", "FSC Mix 90%",
                           "FSC Mix 100%", "FSC", "FSC Recycled", "FSC Mix 70%")),
    FieldSpec("txtFSCBL", "FSC比例", alias="fsc_ratio", group="属性"),
    FieldSpec("txtGBCS", "改版次数", KIND_NUMBER, "revision_count", group="属性"),
    FieldSpec("txtSFYPJ", "是否有配件", KIND_SELECT, "has_parts", group="属性",
              options=_opt("是", "否")),
    FieldSpec("chkXBJF", "不需报价", KIND_CHECKBOX, "no_quote", group="属性"),
    FieldSpec("chkSFBG", "是否变更", KIND_CHECKBOX, "changed_flag", group="属性"),
    FieldSpec("txtSPDJ", "审批单价（SPDJ）", alias="approval_unit_price", group="属性"),
    FieldSpec("txtFS", "份数", KIND_NUMBER, "copies", group="数量"),
    FieldSpec("txtLS", "联数", KIND_NUMBER, "sheet_count", group="数量"),
    FieldSpec("txtFSSL", "FSC数量（无标签）", KIND_NUMBER, "fsc_qty", group="数量",
              note="页面上紧跟『产品翻单类型』，标签为空，含义未确认"),
    FieldSpec("txtLYSL", "联数数量（无标签）", KIND_NUMBER, "lianshu_qty", group="数量",
              note="标签为空，含义未确认"),
    FieldSpec("txtCTBSL", "CTB数量（无标签）", KIND_NUMBER, "ctb_qty", group="数量",
              note="标签为空，含义未确认"),
)

# ══════════════════ d. 描述 / 备注 ══════════════════
_NOTES: tuple[FieldSpec, ...] = (
    FieldSpec("txtCPMS", "产品描述", KIND_TEXTAREA, "product_desc", ("description",),
              group="描述", required=True),
    FieldSpec("txtBGMS", "补充描述", KIND_TEXTAREA, "extra_desc", group="描述"),
    FieldSpec("txtXQNR", "需求内容", KIND_TEXTAREA, "requirement", group="描述"),
    FieldSpec("txtRemark", "备注（明细）", KIND_TEXTAREA, "remark", group="描述"),
    FieldSpec("txtRemark1", "配件描述", KIND_TEXTAREA, "parts_desc", group="描述",
              required=True,
              note="部分记录（是否有配件=否）页面上是只读的"),
    FieldSpec("txtRemark2", "备注2", KIND_TEXTAREA, "remark2", group="描述"),
    FieldSpec("txtRemark3", "修改记录", KIND_TEXTAREA, "modify_log", group="描述",
              required=True),
    FieldSpec("txtRemark4", "检测项目", KIND_TEXTAREA, "inspection", group="描述",
              required=True),
    FieldSpec("txtRemark5", "备注5", KIND_TEXTAREA, "remark5", group="描述"),
    FieldSpec("txtRemark6", "备注6", KIND_TEXTAREA, "remark6", group="描述"),
    FieldSpec("txtRemark7", "备注7", KIND_TEXTAREA, "remark7", group="描述"),
    FieldSpec("txtRemark8", "备注8", KIND_TEXTAREA, "remark8", group="描述"),
    FieldSpec("txtRemark9", "备注9", KIND_TEXTAREA, "remark9", group="描述"),
    FieldSpec("txtRemark10", "备注10", KIND_TEXTAREA, "remark10", group="描述"),
    FieldSpec("txtOldCPMS", "原产品描述", KIND_TEXTAREA, "old_product_desc",
              group="返单", writable=False, note="返单快照，ERP 自动带入"),
    FieldSpec("txtOLD_KHCPBH", "原客户产品编号", alias="old_customer_product_code",
              group="返单"),
    FieldSpec("txtOLD_KHCPMC", "原客户产品名称", alias="old_customer_product_name",
              group="返单"),
    FieldSpec("txtOLD_KHDDH", "返单香港单号", alias="old_hk_order_no", group="返单"),
    FieldSpec("txtOLD_BDBH", "原表单编号", alias="old_bdbh", group="返单"),
    FieldSpec("txtZXTH", "ZXTH（无中文标签）", alias="zxth", group="其他"),
    FieldSpec("txtYSTH", "YSTH（无中文标签）", alias="ysth", group="其他"),
    FieldSpec("txtXX", "XX（无中文标签）", alias="xx", group="其他"),
)

#: 包装备注 txtCPBZ1..10（折叠面板，textarea）
_PACK_NOTES: tuple[FieldSpec, ...] = tuple(
    FieldSpec(f"txtCPBZ{index}", f"包装备注{index}", KIND_TEXTAREA,
              f"packaging_note_{index}", group="描述")
    for index in range(1, 11)
)

# ══════════════════ e. 计价 / 财务 ══════════════════
_PRICING: tuple[FieldSpec, ...] = (
    FieldSpec("txtBZ", "客户目标价币种", KIND_SELECT, "target_price_currency",
              group="计价", options=_opt("人民币", "港币", "美元", "欧元", "日元",
                                        "澳元", "英镑")),
    FieldSpec("txtKHMBDJ", "客户目标价", KIND_NUMBER, "target_price", group="计价"),
    FieldSpec("txtJZFS", "付款方式", KIND_SELECT, "payment_method", group="计价",
              options=_opt("现金", "电汇", "支票", "转账", "香港付款", "信用证(LC)")),
    FieldSpec("txtFKQXID", "付款期限", KIND_SELECT, "payment_term", group="计价",
              note="选项随客户/账号变化（实测 53 项），写库时以页面为准"),
    FieldSpec("txtFPLB", "发票类别", KIND_SELECT, "invoice_type", group="计价",
              options=_opt("增值税(13%)", "增值税(11%)", "增值税(10%)", "增值税(9%)",
                           "增值税(6%)", "增值税(5%)", "增值税(3%)", "增值税(1%)",
                           "普通发票", "国内收据", "香港发票(直接进口)",
                           "香港发票(转厂进口)", "香港发票(直接出口)")),
    FieldSpec("txtFPSL", "发票税率", alias="invoice_tax_rate", group="计价"),
    FieldSpec("txtHL", "汇率", alias="exchange_rate", group="计价"),
    FieldSpec("txtCPDJ", "产品单价", alias="unit_price", group="计价"),
    FieldSpec("txtCPJE", "产品金额", alias="amount", group="计价",
              note="通常由 ERP 按 单价×数量 计算"),
    FieldSpec("txtYSFY", "运输费用", alias="transport_fee", group="计价"),
    FieldSpec("txtQTFY", "其他费用", alias="other_fee", group="计价"),
    FieldSpec("txtHSDJ", "含税单价", alias="taxed_unit_price", group="计价",
              note="通常由 ERP 计算"),
    FieldSpec("txtJSHJ", "价税合计", alias="taxed_total", group="计价",
              note="通常由 ERP 计算"),
    FieldSpec("txtXGBZ", "香港币种", KIND_SELECT, "hk_currency", group="计价",
              options=_opt("人民币", "港币", "美元", "欧元", "日元", "澳元", "英镑")),
    FieldSpec("txtXGHL", "香港汇率", alias="hk_exchange_rate", group="计价"),
    FieldSpec("txtXGCPDJ", "香港产品单价", alias="hk_unit_price", group="计价"),
    FieldSpec("txtXGCPJE", "香港产品金额", alias="hk_amount", group="计价",
              note="通常由 ERP 计算"),
    FieldSpec("txtKHEmail", "客户邮箱", alias="customer_email", group="计价"),
)

# ══════════════════ f. 条形码 / 号码 / 印刷 ══════════════════
_PRINTING: tuple[FieldSpec, ...] = (
    FieldSpec("txtTMLX", "条码类型", KIND_SELECT, "barcode_type", group="号码",
              options=_opt("Code39码", "Codabar码", "Code25码", "ITF25码", "Matrix25码")),
    FieldSpec("txtJYMLX", "校验码类型", KIND_SELECT, "check_digit_type", group="号码",
              options=_opt("模7", "模9", "模11", "模10", "EMS", "顺丰", "平安人寿",
                           "FedEx", "齐家网")),
    FieldSpec("txtQSJYM", "起始校验码", alias="check_digit_from", group="号码"),
    FieldSpec("txtJZJYM", "截止校验码", alias="check_digit_to", group="号码"),
    FieldSpec("txtGZ", "前缀", alias="serial_prefix", group="号码"),
    FieldSpec("txtHZ", "后缀", alias="serial_suffix", group="号码"),
    FieldSpec("txtQSHM", "起始号码", alias="serial_from", group="号码"),
    FieldSpec("txtJZHM", "截止号码", alias="serial_to", group="号码"),
    FieldSpec("txtHMMS", "号码描述", alias="serial_desc", group="号码"),
    FieldSpec("txtMBSL", "每箱数量", KIND_NUMBER, "per_carton_qty", group="号码"),
    FieldSpec("txtQSXH", "起始箱号", alias="carton_no_from", group="号码"),
    FieldSpec("txtJZXH", "截止箱号", alias="carton_no_to", group="号码"),
    FieldSpec("txtLGZL", "来稿资料", KIND_SELECT, "artwork_source", group="印刷",
              options=_opt("样稿", "邮件", "传真")),
    FieldSpec("txtSBFS", "撕边方式", KIND_SELECT, "perforation", group="印刷",
              options=_opt("无撕边", "左撕边", "右撕边", "两边")),
    FieldSpec("txtYSFX", "印刷方向", KIND_SELECT, "print_direction", group="印刷",
              options=_opt("头先出", "尾先出")),
    FieldSpec("txtPYCX", "配页程序", alias="collating_program", group="印刷"),
    FieldSpec("txtCPGYLC", "产品工艺流程图", alias="process_route", group="印刷"),
)

# ══════════════════ g. 数量 / 款数 ══════════════════
_QTY: tuple[FieldSpec, ...] = (
    FieldSpec("txtTZS", "款数", KIND_NUMBER, "cases", group="数量", required=True),
    FieldSpec("txtYYS", "语言数", KIND_NUMBER, "languages", group="数量", required=True),
    FieldSpec("txtCPSL", "产品数量（首档/汇总）", KIND_NUMBER, "qty_total", group="数量",
              note="= 第1档数量；列表页「数量描述」用的就是它"),
)

#: 「数量档位」与「每款数量」两个平行家族（实测 2026-11-12 用 BJXQ261110003 校对）：
#:   * `txtCPSL1..10`   = 第 n 档报价**总量**；列表页只展示首档/第二档/末档，
#:                       对应列「最小值 / 中间值 / 最大值」；`txtCPSL` 是第一档。
#:   * `txtCPMKSL1..10` = 第 n 档的**每款数量**（总量 ÷ 款数，ERP 自动算）。
#:   * `txtCPSLID1..10` = 多语言/多档明细的 GUID，未建时为 -1。
#: 所以「多个数量」不是「多款」——款数由 `txtTZS` 单独控制。
_MULTI_ROW: tuple[FieldSpec, ...] = tuple(
    spec
    for index in range(1, 11)
    for spec in (
        FieldSpec(f"txtCPSL{index}", f"第{index}档报价数量（总量）", KIND_NUMBER,
                  f"qty_{index}", group="数量档位",
                  note="列表页的 最小值/中间值/最大值 就取自首档/第二档/末档"),
        FieldSpec(f"txtCPMKSL{index}", f"第{index}档每款数量", KIND_NUMBER,
                  f"per_case_qty_{index}", group="数量档位",
                  note="= 该档总量 ÷ 款数，通常由 ERP 自动算"),
        FieldSpec(f"txtCPSLID{index}", f"第{index}档数量明细ID",
                  group="数量档位", writable=False,
                  note="多语言/多档数量明细的 GUID，未建时为 -1；本工具不会自动创建"),
    )
)

# ══════════════════ h. 系统字段 ══════════════════
_SYSTEM: tuple[FieldSpec, ...] = (
    FieldSpec("lblID", "明细行ID", group="系统", writable=False),
    FieldSpec("lblPID", "主表ID", group="系统", writable=False),
    FieldSpec("lblQXBH", "权限号", group="系统", writable=False),
    FieldSpec("txtKHID", "客户ID", group="系统", writable=False,
              note="由 create() 按客户资料填入"),
    FieldSpec("txtKHDQ", "客户地区", alias="customer_region", group="系统", writable=False),
    FieldSpec("txtKHDDH", "香港单号", alias="hk_order_no", group="系统", writable=False,
              note="只读；单据编号/香港单号由 ERP 维护"),
    FieldSpec("txtXQZT", "需求状态", group="系统", writable=False),
    FieldSpec("txtWJZT", "文件状态", group="系统", writable=False),
    FieldSpec("txtKBZT", "客板状态", group="系统", writable=False),
    FieldSpec("txtDDBH", "订单编号", alias="order_no", group="系统", writable=False),
    FieldSpec("txtDDBHID", "订单ID", group="系统", writable=False),
    FieldSpec("txtOLD_ID", "原明细ID", group="系统", writable=False),
    FieldSpec("txtBZQRRID", "步骤确认人ID", group="系统", writable=False),
    FieldSpec("txtBZQRR", "步骤确认人", group="系统", writable=False),
    FieldSpec("txtBZQRRQ", "步骤确认日期", group="系统", writable=False),
    FieldSpec("lblgVQXBH", "权限号（只读）", group="系统", writable=False),
    FieldSpec("lblgVSetQXID", "权限号（只读2）", group="系统", writable=False),
    FieldSpec("lblgVID", "ID（只读）", group="系统", writable=False),
    FieldSpec("CBListXQLX$0", "需求类型：报价单", KIND_CHECKBOX, group="系统", writable=False),
    FieldSpec("CBListXQLX$1", "需求类型：白版单", KIND_CHECKBOX, group="系统", writable=False),
    FieldSpec("CBListXQLX$2", "需求类型：客户订单", KIND_CHECKBOX, group="系统", writable=False),
    FieldSpec("CBListXQLX$3", "需求类型：彩版单", KIND_CHECKBOX, group="系统", writable=False),
    FieldSpec("CBListXQLX$4", "需求类型：合同评审", KIND_CHECKBOX, group="系统", writable=False),
    FieldSpec("chkQTDF", "其他（disabled）", KIND_CHECKBOX, group="系统", writable=False),
    FieldSpec("txtQTMC", "其他名称（disabled）", KIND_SELECT, group="系统", writable=False),
    FieldSpec("ChBoxKQTHF", "开票含税复选框", KIND_CHECKBOX, group="系统", writable=False),
    FieldSpec("ChBoxYSXDF", "预收先订复选框", KIND_CHECKBOX, group="系统", writable=False),
)

#: **DOM 首屏可见**的 53 个控件（2026-11-12 抓页面时逐个判断祖先节点有没有
#: `display:none`）。其余 142 个在 `table#TableVisible` 等折叠面板里。
#: 只有出现在这里的控件才允许通过 MCP 上传（见 `FieldSpec.uploadable`）。
VISIBLE_CONTROLS: frozenset[str] = frozenset({
    # 产品标识 / 规格
    "txtCPDL", "Drop_SJFX", "txtCPMC", "txtDWMC", "txtCD", "txtKD", "txtGD",
    # 装订 / 包装 / 物流
    "txtZDFS", "txtBZFS", "txtTBLX", "txtSHFS", "txtZXLB", "txtZBLX", "txtZXFS",
    "txtDKMC", "txtYSLX", "txtSHDQ", "txtKHDQ",
    "chkBSYTB", "chkBSYJZX", "chkZDBJF",
    # 数量（每款一行；汇总值 txtCPSL 是隐藏的，只能通过 qty 间接写）
    "txtTZS", "txtYYS",
    "txtCPSL1", "txtCPSL2", "txtCPSL3", "txtCPSL4", "txtCPSL5",
    "txtCPSL6", "txtCPSL7", "txtCPSL8", "txtCPSL9", "txtCPSL10",
    # 计价（页面上只有目标价这一组）
    "txtBZ", "txtKHMBDJ",
    # 单号 / 交期
    "txtKHCPMC", "txtJHRQ", "txtWCRQ", "txtPXH", "txtKHDDH", "txtOLD_KHDDH",
    # 属性
    "txtFSCMC", "txtSFYPJ", "txtCPSX", "txtCPLX", "chkXBJF",
    # 文本
    "txtECPMC", "txtCPMS", "txtBGMS", "txtRemark", "txtRemark1",
    "txtRemark3", "txtRemark4",
})

#: ASP.NET 状态域，永远不该出现在业务字段里
STATE_FIELDS: frozenset[str] = frozenset({
    "__EVENTTARGET", "__EVENTARGUMENT", "__LASTFOCUS", "__VIEWSTATE",
    "__VIEWSTATEGENERATOR", "__SCROLLPOSITIONX", "__SCROLLPOSITIONY",
    "__VIEWSTATEENCRYPTED", "txtCPLB_SelectedIndex", "txtCPLB_Data",
})

#: 全部控件（顺序 ≈ 页面出现顺序，便于对照）
PRODUCT_FIELDS: tuple[FieldSpec, ...] = (
    _IDENTITY + _PACKAGING + _ATTRIBUTES + _NOTES + _PACK_NOTES + _PRICING
    + _PRINTING + _QTY + _MULTI_ROW + _SYSTEM
)

CONTROL_MAP: dict[str, FieldSpec] = {spec.control: spec for spec in PRODUCT_FIELDS}

#: JSON 字段名 / 口语化别名 → 控件名
ALIAS_MAP: dict[str, str] = {}
for _spec in PRODUCT_FIELDS:
    for _name in (_spec.alias, *_spec.extra_aliases):
        if _name:
            ALIAS_MAP[_name] = _spec.control
    # 控件名本身也可直接当键用
    ALIAS_MAP.setdefault(_spec.control, _spec.control)

#: **可由调用方上传的控件 = 可见 且 可写**。隐藏字段一个都不在里面。
UPLOAD_CONTROLS: frozenset[str] = frozenset(
    spec.control for spec in PRODUCT_FIELDS if spec.uploadable
)

#: 兼容旧名字：与 UPLOAD_CONTROLS 相同
WRITABLE_CONTROLS: frozenset[str] = UPLOAD_CONTROLS

#: 上传清单：别名 → 控件（只含可上传字段），供文档/报错提示用
UPLOAD_ALIASES: dict[str, str] = {
    alias: control
    for alias, control in ALIAS_MAP.items()
    if control in UPLOAD_CONTROLS
}

#: **ERP 保存时服务端强制要求**的明细字段（缺了会被挡在「【XX】不能为空！」）。
#: 来源：逐条实测的必填链（见 README「实测的必填链」）。
#: 注意「产品类别」与「产品数量」不在这里：前者由产品档案自动回填，
#: 后者由 `qty` 自动写 `txtCPSL` + `txtCPSL1`。
REQUIRED_PRODUCT_FIELDS: frozenset[str] = frozenset(
    spec.control for spec in PRODUCT_FIELDS if spec.required
)

#: 必填字段的对外名字（别名），供文档/预览展示
REQUIRED_ALIASES: tuple[str, ...] = tuple(
    spec.alias or spec.control for spec in PRODUCT_FIELDS if spec.required
)


def visible_of(control: str) -> bool:
    """该控件是否在首屏可见。"""
    return control in VISIBLE_CONTROLS


#: 历史上对外暴露的 14 个明细字段（保持向后兼容，值必须与旧版完全一致）
LEGACY_SUB_FIELD_MAP: dict[str, str] = {
    "binding": "txtZDFS",
    "packing": "txtBZFS",
    "pallet": "txtTBLX",
    "delivery": "txtSHFS",
    "delivery_date": "txtJHRQ",
    "expected_date": "txtWCRQ",
    "parts_desc": "txtRemark1",
    "modify_log": "txtRemark3",
    "inspection": "txtRemark4",
    "product_desc": "txtCPMS",
    "attribute": "txtCPSX",
    "product_type": "txtCPLX",
    "cases": "txtTZS",
    "languages": "txtYYS",
}
for _alias, _control in LEGACY_SUB_FIELD_MAP.items():
    assert ALIAS_MAP.get(_alias) == _control, f"别名 {_alias} 与历史定义不一致"

#: 已占用的顶层键（不能当明细字段名用）
RESERVED_ITEM_KEYS: frozenset[str] = frozenset(
    {"name", "spec", "qty", "fields", "allow_readonly", "product_category"}
)


def resolve_control(key: str) -> str | None:
    """把「别名 / 控件名」解析成控件名；未知返回 None。"""
    name = (key or "").strip()
    if not name:
        return None
    if name in CONTROL_MAP:
        return name
    return ALIAS_MAP.get(name)


def alias_of(control: str) -> str:
    spec = CONTROL_MAP.get(control)
    return spec.alias if spec and spec.alias else ""


def suggest(key: str, limit: int = 5) -> list[str]:
    """给未知键找几个候选（先按前缀，再按包含关系）。"""
    text = (key or "").strip().lower()
    if not text:
        return []
    pool = list(ALIAS_MAP)
    starts = [k for k in pool if k.lower().startswith(text[:3])]
    contains = [k for k in pool if text[:3] in k.lower() and k not in starts]
    return (starts + contains)[:limit]


def grouped() -> dict[str, list[FieldSpec]]:
    """按语义分组返回，供 `erp_quote_product_form` 展示。"""
    out: dict[str, list[FieldSpec]] = {}
    for spec in PRODUCT_FIELDS:
        out.setdefault(spec.group or "其他", []).append(spec)
    return out


# ══════════════════ 上传用 JSON Schema ══════════════════
def _prop(spec: FieldSpec) -> dict:
    """把一条 FieldSpec 变成 JSON Schema 的 property 定义。"""
    prop: dict = {"title": spec.label, "x-control": spec.control}
    if spec.kind == KIND_CHECKBOX:
        prop["type"] = "boolean"
    elif spec.kind == KIND_NUMBER:
        prop["type"] = "number"
    elif spec.kind == KIND_SELECT and spec.options:
        prop["type"] = "string"
        prop["enum"] = list(spec.options)
        prop["description"] = "传下拉的显示文字，落库时按真实页面换成提交值"
    elif spec.kind == KIND_SELECT:
        prop["type"] = "string"
        prop["description"] = "下拉选项随客户/账号变化，落库时以真实页面为准"
    elif spec.kind == KIND_DATE:
        prop["type"] = "string"
        prop["examples"] = ["2026-11-20"]
        prop["description"] = "日期字符串，ERP 也接受 2026/11/20"
    elif spec.kind == KIND_TEXTAREA:
        prop["type"] = "string"
    else:
        prop["type"] = "string"
    if spec.note and "description" not in prop:
        prop["description"] = spec.note
    elif spec.note:
        prop["description"] += f"（{spec.note}）"
    return prop


def schema_property(spec: FieldSpec) -> dict:
    """`_prop()` 的公开别名。

    MCP 层要拿同一份 title/description/examples 去生成 pydantic 模型，
    走这个入口就不会出现「文档一套、inputSchema 另一套」。
    """
    return _prop(spec)


#: 示例里几个字段给具体值，比笼统的「示例」更好读
_EXAMPLE_VALUES: dict[str, object] = {
    "unit": "本",
    "product_desc": "四色印刷，精装",
    "extra_desc": "内页 157g 铜版纸",
    "remark": "客户要求加急",
    "parts_desc": "无",
    "modify_log": "新款报价",
    "inspection": "无",
    "product_group": "书版",
    "product_name": "示例产品名称",
    "customer_order_no": "PO-2026-001",
}


def _example_value(spec: FieldSpec) -> object:
    key = spec.alias or spec.control
    if key in _EXAMPLE_VALUES:
        return _EXAMPLE_VALUES[key]
    if spec.kind == KIND_CHECKBOX:
        return True
    if spec.kind == KIND_NUMBER:
        return 1
    if spec.kind == KIND_SELECT and spec.options:
        return next(iter(spec.options))
    if spec.kind == KIND_DATE:
        return "2026-11-20"
    return "示例"


#: 示例载荷里优先体现的业务字段（其余字段不塞示例，避免噪音）
_EXAMPLE_KEYS: tuple[str, ...] = (
    "binding", "packing", "pallet", "delivery", "delivery_date", "expected_date",
    "carton_type", "board_type", "packing_orientation", "no_pallet",
    "parts_desc", "modify_log", "inspection", "product_desc", "attribute",
    "product_type", "cases", "languages", "unit", "fsc_category", "has_parts",
)


def upload_payload_schema(*, include_excluded: bool = True) -> dict:
    """返回 **MCP 上传用**的 `products[0]` JSON Schema + 示例。

    只列 `uploadable`（DOM 可见 且 可写）的字段；隐藏字段与只读字段放在
    `excluded` 里，明确标注「不接受写入」。
    """
    properties: dict[str, dict] = {
        "name": {
            "type": "string",
            "title": "产品名称",
            "description": "先在【产品档案】里模糊检索，必须唯一匹配（精确同名优先）",
        },
        "spec": {
            "type": "string",
            "title": "规格",
            "description": "形如 210*148；只做非空校验，实际落库规格取自产品档案",
            "examples": ["210*148"],
        },
        "qty": {
            "type": "number",
            "title": "产品数量",
            "exclusiveMinimum": 0,
            "description": "同时写隐藏汇总值 txtCPSL 与首屏展示框 txtCPSL1",
        },
        "product_category": {
            "type": "string",
            "title": "产品类别",
            "description": (
                "形如 `27.01;普通BB书`（也接受 `27.01` 或 `普通BB书`）。"
                "产品档案里搜不到时必须给；档案能命中时可省略"
            ),
            "examples": ["27.01;普通BB书", "04.01;STD彩盒"],
        },
    }
    field_props: dict[str, dict] = {}
    for spec in PRODUCT_FIELDS:
        if not spec.uploadable:
            continue
        key = spec.alias or spec.control
        field_props[key] = _prop(spec)
    properties["fields"] = {
        "type": "object",
        "title": "产品明细字段",
        "description": (
            "key 用 alias（本表）或页面控件名（x-control）都行，两者等价；"
            "只接受 DOM 首屏可见的字段，隐藏/只读字段会被拒绝"
        ),
        "additionalProperties": False,
        "properties": field_props,
    }

    example_fields: dict[str, object] = {}
    for spec in PRODUCT_FIELDS:
        if not spec.uploadable:
            continue
        key = spec.alias or spec.control
        if key in _EXAMPLE_KEYS:
            example_fields[key] = _example_value(spec)

    schema: dict = {
        "type": "object",
        "required": ["name", "spec", "qty"],
        "additionalProperties": False,
        "properties": properties,
    }
    payload: dict = {
        "tool": "erp_quote_create",
        "argument": "products[0]",
        "json_schema": schema,
        "example": {
            "name": "示例产品名称",
            "spec": "210*148",
            "qty": 12000,
            "product_category": "27.01;普通BB书",
            "fields": example_fields,
        },
        "allowed": {
            "count": len(field_props),
            "aliases": sorted(field_props),
        },
        "rules": [
            "fields 里只能用上面 allowed.aliases 或对应的页面控件名（x-control）。",
            "product_category 是 products[] 层的键，不是 fields 里的键。",
            "隐藏字段（折叠面板）一律拒绝——隐藏字段不是「可选高级项」，是别的分区的数据。",
            "只读/系统字段（如香港单号、产品数量汇总、数量明细 GUID）同样拒绝。",
            "下拉传显示文字；checkbox 传 true/false；数字字段传 number 或数字字符串。",
            "未知 key 会直接报错并给出相近字段，不会静默丢弃。",
        ],
    }
    if include_excluded:
        hidden = sorted(
            s.control for s in PRODUCT_FIELDS
            if not s.visible and s.control not in STATE_FIELDS
        )
        readonly = sorted(
            s.control for s in PRODUCT_FIELDS if s.visible and not s.writable
        )
        payload["excluded"] = {
            "hidden_count": len(hidden),
            "hidden": hidden,
            "readonly_count": len(readonly),
            "readonly": readonly,
        }
    return payload


__all__ = [
    "ALIAS_MAP",
    "CONTROL_MAP",
    "FieldSpec",
    "KIND_CHECKBOX",
    "KIND_DATE",
    "KIND_NUMBER",
    "KIND_SELECT",
    "KIND_TEXT",
    "KIND_TEXTAREA",
    "LEGACY_SUB_FIELD_MAP",
    "PRODUCT_FIELDS",
    "REQUIRED_ALIASES",
    "REQUIRED_PRODUCT_FIELDS",
    "RESERVED_ITEM_KEYS",
    "STATE_FIELDS",
    "UPLOAD_ALIASES",
    "UPLOAD_CONTROLS",
    "VISIBLE_CONTROLS",
    "WRITABLE_CONTROLS",
    "alias_of",
    "grouped",
    "resolve_control",
    "schema_property",
    "suggest",
    "upload_payload_schema",
    "visible_of",
]
