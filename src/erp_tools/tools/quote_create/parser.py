"""「新增客户报价需求」页（BJ_KHBJXQ/Add.aspx）解析。

两个东西要解析：

1. **空白单据页**：读出一份可提交字段快照 + 三个下拉的「文字 → GUID」映射。
   ASP.NET WebForms 的控件 ID 在不同账号下可能缺失，所以只按语义取值。
2. **客户选择页**：每一行的真实数据挂在 `<tr ondblclick="SelectKHZL(...)">` 的
   39 个位置参数上（第 0 个就是客户 ID）。这是唯一能拿到客户 GUID 的来源。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ...core.aspnet import soup_of

#: `SelectKHZL(KHID, KHBM, KHZL, ...)` 的位置含义（顺序取自页面 JS 定义）
CUSTOMER_FIELDS: tuple[str, ...] = (
    "customer_id",      # 0  KHID
    "customer_code",    # 1  KHBM
    "customer_name",    # 2  KHZL
    "category_id",      # 3  KHLBID
    "category_code",    # 4  KHLBBM
    "category",         # 5  KHLB
    "contact",          # 6  LXR
    "phone",            # 7  LXDH
    "mobile",           # 8  SJ
    "settle_type",      # 9  JZFS
    "invoice_type",     # 10 FPLB
    "tax_rate",         # 11 SL
    "pay_term",         # 12 FKQX
    "pay_term_id",      # 13 FKQXID
    "discount",         # 14 ZKL
    "price_system_id",  # 15 DJTXID
    "price_system",     # 16 DJTX
    "address",          # 17 GSDZ
    "sales_id",         # 18 YGID
    "sales",            # 19 YGXM
    "profit",           # 20 JYLL
    "cash_discount",    # 21 SKZKL
    "currency",         # 22 BZ
    "rate",             # 23 HL
    "fax",              # 24 CZ
    "sale_company",     # 25 XSGS
    "attach_ratio",     # 26 FSBL
    "loss_value",       # 27 SYZ
    "merchandiser_id",  # 28 GDYID
    "merchandiser",     # 29 GDYXM
    "invoice_customer",  # 30 KPKHMC
    "customer_attr",    # 31 KHSX
    "inner_price",      # 32 NJBL
    "sale_company_id",  # 33 XSGSID
    "short_name",       # 34 KHJC
    "prepay_ship",      # 35 YSSHF
    "email",            # 36 Email
    "pick_notice",      # 37 KQTHF
    "prepay_order",     # 38 YSXDF
)

#: 注意用**贪婪**匹配到属性结尾：单价体系这类显示文字里会带英文括号，
#: 例如 `II级(标准)`，非贪婪的 `.*?\)` 会在这里提前截断，导致参数错位。
_SELECT_KHZL = re.compile(r"SelectKHZL\((.*)\)", re.I | re.S)
_QUOTED = re.compile(r"'([^']*)'")

#: 产品档案选择页 `SelectCPZL(...)` 的位置 -> 明细子页控件名
#: 取自 /js/System_Open_Base.js 的 `InputOpenCPZLOnlyWindow(arrArgs)`
PRODUCT_ARG_FIELDS: dict[int, str] = {
    0: "txtCPID", 1: "txtCPBH", 2: "txtCPMC", 3: "txtLBID", 4: "txtLBBM",
    5: "txtCPLB", 6: "txtDWMC", 7: "txtGG", 8: "txtKZ", 9: "txtCD",
    10: "txtKD", 11: "txtGD", 18: "txtCPDL", 19: "txtKHCPBH", 20: "txtKHCPMC",
    21: "txtZXTH", 22: "txtYSTH", 23: "txtXX", 24: "txtGGLX", 25: "txtMBSL",
    26: "txtRemark", 40: "txtGGMS", 42: "txtFSCBL", 43: "txtQTKZ",
    46: "txtCNB", 47: "txtDNB", 48: "txtCPZKCD", 49: "txtCPZKKD",
    62: "txtBQJBFX", 63: "txtBQJNJ", 64: "txtBQJWJ", 65: "txtMJZGS", 66: "txtHBGP",
}
#: 产品选择页一行最多 67 个位置参数
PRODUCT_ARGS = 67

_SELECT_CPZL = re.compile(r"SelectCPZL\((.*)\)", re.I | re.S)

#: 明细行的「点击产品名称打开编辑页」入口：
#: `OpenInputCPZLData('<CPID>','<PID>','0')`，第三个参数 0=修改 / 1=删除
_DETAIL_EDIT = re.compile(
    r"OpenInputCPZLData\(\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'0'\s*\)", re.I
)


def parse_detail_rows(html: str) -> list[dict[str, str]]:
    """解析明细区里可点击的产品行：`{cpid, pid, name}`。"""
    soup = soup_of(html)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for el in soup.find_all(True):
        onclick = el.get("onclick") or ""
        if "OpenInputCPZLData" not in onclick:
            continue
        match = _DETAIL_EDIT.search(onclick)
        if not match:
            continue
        cpid, pid = match.group(1), match.group(2)
        if not cpid or cpid in seen:
            continue
        seen.add(cpid)
        rows.append({"cpid": cpid, "pid": pid, "name": el.get_text(strip=True)})
    return rows



#: 空白页里值得回给调用方的业务字段（不含 __VIEWSTATE 之类）。
#: 实测 `Add.aspx` 只有这些：客户名称只读、客户 ID/编码由客户资料页选、
#: 制单人/制单时间 disabled；**没有客户单号**（那是明细级 `txtKHCPMC`）。
HEADER_FIELDS: tuple[str, ...] = (
    "txtBDBH", "txtKHID", "txtKHMC", "txtKHBM",
    "txtYGID", "txtYGXM", "txtGDYXM", "txtDJTX",
    "txtLXR", "txtLXDH", "txtSJ", "txtCZ", "txtZKL",
    "txtRemark", "txtCPMX",
)

#: 三个真正的 `<select>`：name -> 提交值
SELECT_FIELDS = ("txtYGXM", "txtGDYXM", "txtDJTX")


@dataclass(frozen=True)
class CustomerOption:
    """客户选择页的一行。字段顺序见 `CUSTOMER_FIELDS`。"""

    values: dict[str, str] = field(default_factory=dict)

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key, default) or default

    def as_dict(self) -> dict[str, str]:
        return dict(self.values)


@dataclass(frozen=True)
class NewQuoteForm:
    """空白新增页的可提交状态。"""

    url: str
    fields: dict[str, str]
    selects: dict[str, dict[str, str]]
    textareas: dict[str, str]
    readonly: tuple[str, ...]

    def resolve_select(self, name: str, label: str) -> str | None:
        """把「张三」这类显示文字解析成要提交的 GUID。"""
        options = self.selects.get(name) or {}
        text = (label or "").strip()
        if not text:
            return None
        if text in options:
            return options[text]
        # 允许直接传 GUID
        if text in options.values():
            return text
        return None


# ══════════════════ 解析 ══════════════════
def parse_new_form(html: str, *, url: str = "") -> NewQuoteForm:
    """解析空白「新增报价需求」页。"""
    soup = soup_of(html)

    fields: dict[str, str] = {}
    readonly: list[str] = []

    for el in soup.find_all("input"):
        name = el.get("name")
        if not name or name.startswith("gridView$"):
            continue
        if (el.get("type") or "").lower() in ("image", "submit", "button", "reset", "file"):
            continue
        if (el.get("type") or "").lower() == "checkbox" and not el.has_attr("checked"):
            continue
        fields[name] = el.get("value") or ""
        if el.has_attr("readonly") or el.has_attr("disabled"):
            readonly.append(name)

    selects: dict[str, dict[str, str]] = {}
    for sel in soup.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        options: dict[str, str] = {}
        for opt in sel.find_all("option"):
            label = opt.get_text(strip=True)
            value = opt.get("value") or ""
            if label:
                options.setdefault(label, value)
        selects[name] = options
        current = sel.find("option", selected=True) or sel.find("option")
        fields[name] = (current.get("value") or "") if current is not None else ""

    textareas: dict[str, str] = {}
    for ta in soup.find_all("textarea"):
        name = ta.get("name")
        if name:
            textareas[name] = ta.get_text() or ""
            fields[name] = textareas[name]

    return NewQuoteForm(
        url=url,
        fields=fields,
        selects=selects,
        textareas=textareas,
        readonly=tuple(sorted(set(readonly))),
    )


def parse_customer_rows(html: str) -> list[CustomerOption]:
    """解析客户选择页的结果行（数据挂在 `ondblclick=SelectKHZL(...)` 上）。"""
    soup = soup_of(html)
    out: list[CustomerOption] = []

    for tr in soup.find_all("tr"):
        onclick = tr.get("ondblclick") or tr.get("onclick") or ""
        match = _SELECT_KHZL.search(onclick)
        if not match:
            continue
        args = _QUOTED.findall(match.group(1))
        if not args or not args[0].strip():
            continue          # 空占位行（查不到时 ERP 会返回一行全空）
        values = {
            key: (args[i] if i < len(args) else "")
            for i, key in enumerate(CUSTOMER_FIELDS)
        }
        out.append(CustomerOption(values=values))
    return out


@dataclass(frozen=True)
class ProductOption:
    """产品档案选择页的一行（`SelectCPZL(67 个位置参数)`）。"""

    args: tuple[str, ...] = ()

    def get(self, index: int, default: str = "") -> str:
        return self.args[index] if index < len(self.args) else default

    @property
    def product_id(self) -> str:
        return self.get(0)

    @property
    def code(self) -> str:
        return self.get(1)

    @property
    def name(self) -> str:
        return self.get(2)

    @property
    def category(self) -> str:
        return self.get(5)

    @property
    def unit(self) -> str:
        return self.get(6)

    @property
    def spec(self) -> str:
        return self.get(7)

    def page_fields(self) -> dict[str, str]:
        """转成明细子页可直接提交的字段（只保留非空项）。"""
        out: dict[str, str] = {}
        for index, field in PRODUCT_ARG_FIELDS.items():
            value = self.get(index)
            if value:
                out[field] = value
        return out


def parse_product_rows(html: str) -> list[ProductOption]:
    """解析产品档案选择页的数据行。"""
    soup = soup_of(html)
    out: list[ProductOption] = []
    for tr in soup.find_all("tr"):
        onclick = tr.get("ondblclick") or tr.get("onclick") or ""
        match = _SELECT_CPZL.search(onclick)
        if not match:
            continue
        args = _QUOTED.findall(match.group(1))
        if not args or not args[0].strip():
            continue
        out.append(ProductOption(args=tuple(args)))
    return out


def parse_rows_count(html: str) -> int | None:
    """读取 `lblRowsCount`，用于区分「0 条」和「一行空占位」。"""
    soup = soup_of(html)
    el = soup.find("span", {"id": "lblRowsCount"})
    if el is None:
        return None
    try:
        return int(el.get_text(strip=True))
    except ValueError:
        return None


# ══════════════════ 产品明细子页（Add_CPZL.aspx）控件清单 ══════════════════
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
_SKIP_INPUT_TYPES = ("image", "submit", "button", "reset", "file")


@dataclass(frozen=True)
class ProductControl:
    """明细子页里的一个控件（原样读出，不做语义映射）。"""

    control: str
    tag: str
    kind: str                     # text / number / select / textarea / checkbox
    value: str = ""
    readonly: bool = False
    disabled: bool = False
    hidden_input: bool = False    # <input type="hidden">
    css_hidden: bool = False      # 祖先节点 display:none（折叠面板）
    options: dict[str, str] = field(default_factory=dict)


def _css_hidden(el) -> bool:
    node = el.parent
    while node is not None:
        if _HIDDEN_STYLE.search(node.get("style") or ""):
            return True
        if node.has_attr("hidden"):
            return True
        node = node.parent
    return False


def _input_kind(el) -> str:
    itype = (el.get("type") or "text").lower()
    if itype == "checkbox":
        return "checkbox"
    return "text"


def parse_product_controls(html: str) -> list[ProductControl]:
    """把明细子页的**全部**表单控件读出来（含折叠面板里的）。

    页面 DOM 里有 205 个控件（2026-11 实测），但只有 53 个在首屏可见，
    其余在 `table#TableVisible` 这类 `display:none` 容器里。是否可见交给
    `fields.py` 的语义标注，这里只如实反映 DOM。
    """
    soup = soup_of(html)
    out: list[ProductControl] = []
    seen: set[str] = set()
    for el in soup.find_all(["input", "select", "textarea"]):
        name = el.get("name")
        if not name or name in seen:
            continue
        itype = (el.get("type") or "").lower()
        if el.name == "input" and itype in _SKIP_INPUT_TYPES:
            continue
        seen.add(name)
        if el.name == "select":
            options: dict[str, str] = {}
            for opt in el.find_all("option"):
                label = opt.get_text(strip=True)
                if label:
                    options.setdefault(label, opt.get("value") or "")
            current = el.find("option", selected=True)
            out.append(ProductControl(
                control=name, tag="select", kind="select",
                value=(current.get("value") or "") if current is not None else "",
                readonly=el.has_attr("readonly"), disabled=el.has_attr("disabled"),
                css_hidden=_css_hidden(el), options=options,
            ))
            continue
        if el.name == "textarea":
            out.append(ProductControl(
                control=name, tag="textarea", kind="textarea",
                value=el.get_text() or "",
                readonly=el.has_attr("readonly"), disabled=el.has_attr("disabled"),
                css_hidden=_css_hidden(el),
            ))
            continue
        out.append(ProductControl(
            control=name, tag="input", kind=_input_kind(el),
            value=el.get("value") or "",
            readonly=el.has_attr("readonly"), disabled=el.has_attr("disabled"),
            hidden_input=itype == "hidden", css_hidden=_css_hidden(el),
        ))
    return out


@dataclass(frozen=True)
class CategoryOption:
    """产品类别树的一个节点。

    `Text` 形如 `27.01;普通BB书`，`Value` 是类别 GUID（提交时写 `txtLBID`）。
    """

    lbid: str
    text: str
    code: str = ""
    name: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"lbid": self.lbid, "code": self.code, "name": self.name, "text": self.text}


#: 类别树有两种渲染方式，都要认：
#: 1. 产品档案选择页把每个节点画成链接：
#:    `href="javascript:OnTreeView1Click('27.01;9e132616-...')" ...>普通BB书</a>`
#: 2. 明细子页把整棵树塞进 JS：`[['Value','<GUID>'],['Text','27.01;普通BB书']]`
_TREE_LINK = re.compile(
    r"OnTreeView1Click\(\s*(?:&#39;|')\s*([0-9A-Za-z.\-]+)\s*;\s*([0-9a-fA-F-]{36})\s*(?:&#39;|')\s*\)"
    r"[^>]*>([^<]*)</a>"
)
_TREE_NODE = re.compile(
    r"\['Value','([0-9a-fA-F-]{36})'\]\s*,\s*\['Text','([^']*)'\]"
)


def _clean(text: str) -> str:
    return " ".join((text or "").replace("\\'", "'").split())


def parse_category_tree(html: str) -> list[CategoryOption]:
    """从页面里抽出完整的产品类别树。

    实测 2026-11-12：`Base_CPZL_Select.aspx`（产品档案选择页）用链接形式渲染了
    全部 90 个节点，`Add_CPZL.aspx`（明细子页）则把同一棵树内嵌进 JS；
    两种来源的 GUID 与历史单据里的 `txtLBID` 完全一致。
    """
    out: list[CategoryOption] = []
    seen: set[str] = set()
    page = html or ""

    for code, lbid, name in _TREE_LINK.findall(page):
        if lbid in seen:
            continue
        seen.add(lbid)
        clean_name = _clean(name)
        out.append(CategoryOption(
            lbid=lbid, text=f"{code};{clean_name}", code=code, name=clean_name,
        ))

    for lbid, text in _TREE_NODE.findall(page):
        if lbid in seen:
            continue
        raw = _clean(text)
        if not raw:
            continue
        seen.add(lbid)
        code, _, name = raw.partition(";")
        out.append(CategoryOption(
            lbid=lbid, text=raw, code=code.strip(), name=name.strip(),
        ))
    return out


__all__ = [
    "CUSTOMER_FIELDS", "CategoryOption", "CustomerOption", "HEADER_FIELDS",
    "NewQuoteForm", "parse_category_tree", "parse_customer_rows", "parse_new_form",
    "parse_product_controls", "parse_rows_count", "ProductControl", "SELECT_FIELDS",
]
