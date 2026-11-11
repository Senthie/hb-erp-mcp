"""「新增客户报价需求」的业务服务层。

设计取向（重要）：

* **只做可验证的部分**。打开空白页、检索客户、组装并校验提交体——这些都已在真实
  ERP 上跑通。
* **不做未经允许的写操作**。`btn_SonNew`（新增产品明细行）在纯 HTTP 回发下**不产生
  行**（实测：回发返回 200 但 `td#tdCPZLLIst` 内 input 数仍为 0），保存协议尚未验证，
  因此本模块**不提供直接落库**的入口，只返回「将会提交什么」。
* 任何失败都以可读的中文异常抛出，便于 MCP 侧转成 `ToolError`。
"""

from __future__ import annotations

import copy
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

from ...core.aspnet import soup_of
from ...core.auth import AuthManager
from ...core.errors import ErpError, ParseError
from ...core.htmlutil import get_input_value
from ..quote_index.tool import form_snapshot
from .parser import (
    HEADER_FIELDS,
    SELECT_FIELDS,
    CustomerOption,
    NewQuoteForm,
    ProductOption,
    parse_customer_rows,
    parse_detail_rows,
    parse_new_form,
    parse_product_rows,
    parse_rows_count,
)

PAGE_PATH = "BaoJia/BJ_KHBJXQ/Add.aspx"
SUB_PAGE_PATH = "BaoJia/BJ_KHBJXQ/Add_CPZL.aspx"
CUSTOMER_PICKER_PATH = "Base_Select/Base_KHZL_Select.aspx"
PRODUCT_PICKER_PATH = "Base_Select/Base_CPZL_Select.aspx"

#: 子页保存时 ERP 会把这些提示写在 alert() / $.ligerDialog.error() 里
_VALIDATION_WORDS = ("不能为空", "请选择", "请输入", "不存在", "重复", "已存在")
_ALERT_RE = re.compile(
    r"(?:alert\(\s*[\"']|\.ligerDialog\.(?:error|alert|warn)\(\s*')(.{4,220}?)[\"']",
    re.S,
)

#: 产品明细子页里「业务上必填」的字段：入参名 -> 页面控件
SUB_FIELD_MAP: dict[str, str] = {
    "binding": "txtZDFS",          # 装订方式
    "packing": "txtBZFS",          # 包装方式
    "pallet": "txtTBLX",           # 台板类型
    "delivery": "txtSHFS",         # 送货方式
    "delivery_date": "txtJHRQ",    # 交货日期
    "expected_date": "txtWCRQ",    # 预计下单日期
    "parts_desc": "txtRemark1",    # 配件描述
    "modify_log": "txtRemark3",    # 修改记录
    "inspection": "txtRemark4",    # 检测项目
    "product_desc": "txtCPMS",     # 产品描述
    "attribute": "txtCPSX",        # 产品属性
    "product_type": "txtCPLX",     # 产品类型
    "cases": "txtTZS",             # 款数
    "languages": "txtYYS",         # 语言数
}

#: 页面 `<select>` -> (对外字段名, 中文名)
SELECT_LABELS: dict[str, tuple[str, str]] = {
    "txtYGXM": ("sales", "业务员"),
    "txtGDYXM": ("merchandiser", "跟单员"),
    "txtDJTX": ("price_system", "单价体系"),
}

#: 对外字段名 / 中文名 / 控件名 -> 页面控件名
SELECT_ALIASES: dict[str, str] = {}
for _field, (_alias, _label) in SELECT_LABELS.items():
    SELECT_ALIASES[_alias.lower()] = _field
    SELECT_ALIASES[_label] = _field
    SELECT_ALIASES[_field.lower()] = _field

#: 「请选择」这类占位项不算真实选项
_PLACEHOLDER_VALUES = {"", "-0-", "-1", "0"}
_PLACEHOLDER_NAMES = {"<--请选择-->", "请选择", "--请选择--"}

#: 下拉选项缓存：权限/人员变动不频繁，短 TTL 换掉一次 1~2 秒的页面请求
_OPTIONS_TTL = 300.0
_options_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_options_lock = threading.Lock()


def clear_options_cache() -> None:
    """清空下拉选项缓存（测试与「强制刷新」用）。"""
    with _options_lock:
        _options_cache.clear()


#: 客户行字段 -> 单据页字段（能直接回填的那些）
CUSTOMER_TO_HEADER = {
    "customer_id": "txtKHID",
    "customer_code": "txtKHBM",
    "customer_name": "txtKHMC",
    "sales_id": "txtYGID",
    "sales": "txtYGXM",
    "merchandiser": "txtGDYXM",
    "price_system_id": "txtDJTX",
    "discount": "txtZKL",
    "contact": "txtLXR",
    "phone": "txtLXDH",
    "mobile": "txtSJ",
    "fax": "txtCZ",
}

#: 页面没有任何 required 标记；这些是按业务常识定的最小必填集
REQUIRED_INPUTS = ("customer", "sales", "products")


#: 目标字段是 `<select>` 时，必须把显示文字换成 GUID，不能直接回填
SELECT_TARGETS = frozenset({"txtYGXM", "txtGDYXM", "txtDJTX"})


def _employee_union(fields: dict[str, Any]) -> list[dict[str, Any]]:
    """把业务员与跟单员合并成一张人名单，并标出各自能担任的角色。"""
    merged: dict[str, dict[str, Any]] = {}
    for alias in ("sales", "merchandiser"):
        for item in (fields.get(alias) or {}).get("options") or []:
            key = item.get("id") or item.get("name") or ""
            if not key:
                continue
            record = merged.setdefault(key, {
                "id": key,
                "name": item.get("name", ""),
                "can_sales": False,
                "can_merchandiser": False,
            })
            record[f"can_{alias}"] = True
    return sorted(merged.values(), key=lambda r: (r["name"], r["id"]))


def _filter_options(payload: dict[str, Any], wanted: str | None) -> dict[str, Any]:
    if wanted is None:
        return payload
    alias = SELECT_LABELS[wanted][0]
    out = dict(payload)
    out["fields"] = {alias: payload.get("fields", {}).get(alias, {})}
    out["filtered_by"] = wanted
    return out


def _control_names(html: str) -> set[str]:
    """页面里可提交的控件名（含 select / textarea）。"""
    soup = soup_of(html)
    names: set[str] = set()
    for el in soup.find_all(["input", "select", "textarea"]):
        name = el.get("name")
        if name:
            names.add(name)
    return names


def _select_value(html: str, control: str, label: str) -> str | None:
    """把下拉的显示文字换成提交值。"""
    sel = soup_of(html).find("select", {"name": control})
    text = (label or "").strip()
    if sel is None or not text:
        return None
    for opt in sel.find_all("option"):
        if opt.get_text(strip=True) == text:
            return opt.get("value") or ""
    return None


def _fmt_qty(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(number)


def float_or_none(value: Any) -> float | None:
    """把页面回读的字符串（可能是 `3000` / `3000个`）变成数字，失败返回 None。"""
    text = str(value or "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:  # pragma: no cover - 正则已保证能转
        return None


def _is_placeholder(value: str | None) -> bool:
    """判断一个控件是不是还停在「<--请选择-->」之类的占位值上。"""
    text = (value or "").strip()
    return not text or text.startswith("<--")


def _is_placeholder(value: str | None) -> bool:
    """判断一个控件是不是还停在「<--请选择-->」之类的占位值上。"""
    text = (value or "").strip()
    return not text or text.startswith("<--")


def _read_value(html: str, control: str) -> str:
    """从页面里读一个控件的当前值（兼容 input / textarea / select）。"""
    soup = soup_of(html)
    el = soup.find("textarea", {"name": control})
    if el is not None:
        return (el.get_text() or "").strip()
    sel = soup.find("select", {"name": control})
    if sel is not None:
        opt = sel.find("option", selected=True)
        return (opt.get("value") or "") if opt is not None else ""
    el = soup.find("input", {"name": control})
    return (el.get("value") or "") if el is not None else ""


def _find_validation_message(html: str) -> str | None:
    """从回发页面里找 ERP 的业务提示（例如「请选择【产品类别】的数据！」）。"""
    for match in _ALERT_RE.finditer(html or ""):
        text = " ".join(match.group(1).split())
        if any(word in text for word in _VALIDATION_WORDS):
            return text
    soup = soup_of(html or "")
    msg = soup.find("input", {"id": "lblShowMsg"})
    if msg is not None:
        value = (msg.get("value") or "").strip()
        if value and any(word in value for word in _VALIDATION_WORDS):
            return value
    return None


class QuoteCreateService:
    """新增报价需求的只读抽象 + 提交体组装。"""

    def __init__(self, auth: AuthManager) -> None:
        self.auth = auth

    # ══════════════════ 地址 ══════════════════
    def _page_url(self) -> str:
        qxid = self.auth.cfg.qxid_quote or self.auth.qxid_for(PAGE_PATH)
        uid = self.auth.user_id or ""
        url = f"/Admin/{PAGE_PATH}?QXID={qxid}&MenuTree=1"
        return f"{url}&LoginUserID={uid}" if uid else url

    def _picker_url(self) -> str:
        return f"/Admin/{CUSTOMER_PICKER_PATH}"

    # ══════════════════ 1. 空白页 ══════════════════
    def _open_form_html(self) -> tuple[str, str]:
        url = self._page_url()
        resp = self.auth.client.get(url)
        if resp.status_code != 200:
            raise ParseError(f"新增页打开失败（HTTP {resp.status_code}）")
        if "__VIEWSTATE" not in resp.text:
            raise ParseError("新增页没有 __VIEWSTATE，可能被重定向到登录页或权限不足")
        return resp.text, url

    def open_form(self) -> NewQuoteForm:
        html, url = self._open_form_html()
        return parse_new_form(html, url=url)

    def describe_form(self) -> dict[str, Any]:
        """给调用方看的「页面能力说明」。"""
        form = self.open_form()
        return {
            "url": form.url,
            "header_fields": {
                name: {
                    "value": form.fields.get(name, ""),
                    "readonly": name in form.readonly,
                }
                for name in HEADER_FIELDS
                if name in form.fields
            },
            "selects": {
                name: {
                    "options": form.selects.get(name, {}),
                    "count": len(form.selects.get(name, {})),
                }
                for name in SELECT_FIELDS
                if name in form.selects
            },
            "textareas": sorted(form.textareas),
            "notes": [
                "txtBDBH / txtKHMC 为只读：表单编号由 ERP 生成，客户只能通过客户资料页选择。",
                "业务员/跟单员/单价体系必须提交下拉项的 GUID，不能提交显示文字。",
                "页面没有任何 required 标记；本工具按「客户 + 业务员 + 产品明细」做最小校验。",
            ],
            "submit_supported": True,
            "submit_notes": (
                "写入是三步：① 保存主表（btn_SonNew 会先建主表）② 产品子页保存明细 "
                "③ 主表保存。调用 erp_quote_create 时必须传 confirm=true。"
            ),
        }

    # ══════════════════ 1b. 下拉选项（业务员 / 跟单员 / 单价体系）══════════════════
    def list_options(self, field: str | None = None, *,
                     refresh: bool = False) -> dict[str, Any]:
        """返回当前账号在新增页里能选到的业务员 / 跟单员 / 单价体系。

        选项是**按账号权限实时读取**的，不同账号可能不一样；默认缓存 300 秒。
        `field` 可只取一个维度（`sales` / `merchandiser` / `price_system`，也接受中文名）。
        """
        wanted: str | None = None
        if field:
            raw = str(field).strip()
            wanted = SELECT_ALIASES.get(raw) or SELECT_ALIASES.get(raw.lower())
            if wanted is None:
                raise ParseError(
                    f"未知的下拉 {field!r}；可用：sales/业务员、"
                    "merchandiser/跟单员、price_system/单价体系"
                )

        cache_key = (self.auth.cfg.username or "anonymous", self._page_url())
        now = time.time()
        if not refresh:
            with _options_lock:
                hit = _options_cache.get(cache_key)
            if hit is not None and now - hit[0] < _OPTIONS_TTL:
                payload = copy.deepcopy(hit[1])
                payload["cached"] = True
                payload["age_seconds"] = int(now - hit[0])
                return _filter_options(payload, wanted)

        form = self.open_form()
        fields: dict[str, Any] = {}
        for control, (alias, label) in SELECT_LABELS.items():
            options = form.selects.get(control) or {}
            items = [
                {"name": name, "id": value}
                for name, value in options.items()
                if value not in _PLACEHOLDER_VALUES and name not in _PLACEHOLDER_NAMES
            ]
            fields[alias] = {
                "label": label,
                "page_field": control,
                "count": len(items),
                "options": items,
            }

        payload = {
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cached": False,
            "age_seconds": 0,
            "fields": fields,
            "employees": _employee_union(fields),
            "notes": [
                "选项按当前 api-key 对应账号的权限实时读取，不同账号数量可能不同。",
                "已剔除「<--请选择-->」这类占位项（值为 -0-）。",
                "employees 是业务员与跟单员的合并视图；只做跟单的同事 can_sales=false。",
                "选定客户后页面可能按客户再次过滤跟单员，最终以提交时的页面为准。",
                f"结果缓存 {int(_OPTIONS_TTL)} 秒，传 refresh=true 可强制重读。",
            ],
        }
        with _options_lock:
            _options_cache[cache_key] = (now, copy.deepcopy(payload))
        return _filter_options(payload, wanted)

    # ══════════════════ 2. 客户检索 ══════════════════
    def _search_customer_rows(self, keyword: str) -> tuple[list[CustomerOption], int | None]:
        """客户资料页的 `txtKey` 实测不可用（返回空占位行），所以取全量再本地过滤。"""
        url = self._picker_url()
        resp = self.auth.client.get(url)
        if resp.status_code != 200:
            raise ParseError(f"客户资料页打开失败（HTTP {resp.status_code}）")

        body = form_snapshot(resp.text)
        body.update({
            "txtKey": "",
            "txtEvenPageDataSize": "500",
            "btn_Search": "搜索",
        })
        resp = self.auth.client.post(url, data=body)
        if resp.status_code >= 500:
            raise ParseError(f"客户检索失败（HTTP {resp.status_code}）")

        rows = parse_customer_rows(resp.text)
        total = parse_rows_count(resp.text)

        text = (keyword or "").strip().lower()
        if text:
            rows = [
                row for row in rows
                if text in row.get("customer_name").lower()
                or text in row.get("customer_code").lower()
                or text in row.get("short_name").lower()
            ]
        return rows, total

    def search_customers(self, keyword: str = "", *, limit: int = 20) -> dict[str, Any]:
        """在客户资料页检索。先取全量（最多 500 行）再按名称/编码/简称本地过滤。"""
        rows, total = self._search_customer_rows(keyword)
        size = max(1, min(int(limit or 20), 50))
        fetched = len(rows)

        return {
            "keyword": keyword or "",
            "total": total,
            "mode": "local-filter" if keyword else "all",
            "count": min(fetched, size),
            "fetched": fetched,
            "page_size": 500,
            "customers": [
                {
                    "customer_id": row.get("customer_id"),
                    "customer_code": row.get("customer_code"),
                    "customer_name": row.get("customer_name"),
                    "short_name": row.get("short_name"),
                    "contact": row.get("contact"),
                    "phone": row.get("phone"),
                    "mobile": row.get("mobile"),
                    "sales": row.get("sales"),
                    "merchandiser": row.get("merchandiser"),
                    "discount": row.get("discount"),
                    "currency": row.get("currency"),
                }
                for row in rows[:size]
            ],
            "truncated": bool((total or 0) > fetched or fetched > size),
            "hint": None if rows else (
                "没有匹配结果。可按客户名称/编码/简称的片段重试（服务端 txtKey 不可用，这里是本地过滤）。"
            ),
        }

    def find_customer(self, keyword: str) -> CustomerOption:
        """按名称/简称找唯一客户；0 条或多条都报错，避免误用。"""
        result = self.search_customers(keyword, limit=50)
        rows = result["customers"]
        if not rows:
            raise ParseError(f"找不到客户 {keyword!r}")
        if len(rows) > 1:
            names = "、".join(r["customer_name"] for r in rows[:5])
            raise ParseError(
                f"客户 {keyword!r} 匹配到 {len(rows)} 条（{names}…），请给出更精确的名称或客户编码"
            )
        return CustomerOption(values={k: v or "" for k, v in rows[0].items()})

    # ══════════════════ 3. 组装并校验 ══════════════════
    def prepare(
        self,
        *,
        customer: str | None = None,
        sales: str | None = None,
        products: list[dict[str, Any]] | None = None,
        contact: str | None = None,
        phone: str | None = None,
        mobile: str | None = None,
        fax: str | None = None,
        discount: str | None = None,
        remark: str | None = None,
        product_memo: str | None = None,
    ) -> dict[str, Any]:
        """把调用方参数组装成「将要提交的字段」，并给出校验结果。

        **不会向 ERP 写任何东西**，也不执行 `btnSave`。
        """
        errors: list[str] = []
        warnings: list[str] = []

        form = self.open_form()
        header: dict[str, str] = {}

        # ---- 客户 ----
        picked: CustomerOption | None = None
        if not customer:
            errors.append("缺少 customer：客户名称（可先用 erp_quote_customer_search 查询）")
        else:
            picked = self.find_customer(customer)
            for src, target in CUSTOMER_TO_HEADER.items():
                raw = picked.get(src)
                if not raw or target not in form.fields:
                    continue
                if target in SELECT_TARGETS:
                    # 下拉必须提交 GUID：客户资料里带的通常是姓名
                    resolved = form.resolve_select(target, raw)
                    if resolved and resolved != "-0-":
                        header[target] = resolved
                    continue
                header[target] = raw
            if not header.get("txtKHID"):
                errors.append(f"客户 {customer!r} 没有返回客户 ID，无法提交")

        # ---- 业务员（下拉必须提交 GUID）----
        sales_label = sales or (picked.get("sales") if picked else "")
        if not sales_label:
            errors.append("缺少 sales：业务员姓名（客户资料里没有默认值时必须显式给出）")
        else:
            value = form.resolve_select("txtYGXM", sales_label)
            if value is None or value == "-0-":
                available = "、".join(k for k in form.selects.get("txtYGXM", {}) if k.startswith("<") is False)
                errors.append(f"业务员 {sales_label!r} 不在页面下拉里；可用：{available or '(页面无此下拉)'}")
            else:
                header["txtYGXM"] = value

        # ---- 可选表头 ----
        if contact:
            header["txtLXR"] = contact
        if phone:
            header["txtLXDH"] = phone
        if mobile:
            header["txtSJ"] = mobile
        if fax:
            header["txtCZ"] = fax
        if discount:
            header["txtZKL"] = str(discount)
        if remark:
            header["txtRemark"] = remark
        if product_memo:
            header["txtCPMX"] = product_memo

        # ---- 产品明细 ----
        items = list(products or [])
        if not items:
            errors.append("缺少 products：至少一条 {name, spec, qty}")
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(items, start=1):
            name = str(item.get("name") or "").strip()
            spec = str(item.get("spec") or "").strip()
            qty = item.get("qty")
            if not name:
                errors.append(f"第 {index} 条明细缺少 name（产品名称）")
            if not spec:
                errors.append(f"第 {index} 条明细缺少 spec（规格，形如 210*148）")
            try:
                qty_value = float(str(qty).strip())
                if qty_value <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"第 {index} 条明细的 qty 必须是正数")
                qty_value = 0
            normalized.append({
                "name": name,
                "spec": spec,
                "qty": qty_value,
                "description": str(item.get("description") or ""),
                "remark": str(item.get("remark") or ""),
                **{
                    key: item.get(key)
                    for key in SUB_FIELD_MAP
                    if item.get(key) not in (None, "")
                },
            })

        # 只保留页面真的有的字段，避免提交未知控件
        header = {k: v for k, v in header.items() if k in form.fields}
        missing_from_page = [k for k in ("txtKHID", "txtYGXM") if k not in form.fields]
        if missing_from_page:
            warnings.append(
                f"页面缺少控件 {', '.join(missing_from_page)}；该账号可能没有新增权限"
            )

        return {
            "ok": not errors,
            "errors": errors,
            "warnings": warnings,
            "customer": picked.as_dict() if picked else None,
            "header": header,
            "products": normalized,
            "submit_supported": True,
            "next": (
                "校验已通过。真正写入请调用 erp_quote_create（confirm=true）："
                "它会在 ERP 里建主表、建产品明细并返回新的表单编号。"
            ),
        }


    # ══════════════════ 4. 产品档案检索 ══════════════════
    def _search_product_rows(self, keyword: str) -> tuple[list[ProductOption], int | None]:
        """按「产品名称」检索产品档案。

        该页的 `txtKey` 默认绑定的是「产品类别」(DropField=CPLB)，所以必须显式改成
        CPMC；另外页面默认只查最近 7 天，要放宽日期区间否则老产品搜不到。
        """
        url = f"/Admin/{PRODUCT_PICKER_PATH}"
        resp = self.auth.client.get(url)
        if resp.status_code != 200:
            raise ParseError(f"产品档案页打开失败（HTTP {resp.status_code}）")
        body = form_snapshot(resp.text)
        body.update({
            "txtKey": keyword or "",
            "DropField": "CPMC",
            "txtKSRQ": "2000-01-01",
            "txtJZRQ": time.strftime("%Y-%m-%d"),
            "txtEvenPageDataSize": "50",
            "btn_Search": "搜索",
        })
        resp = self.auth.client.post(url, data=body)
        if resp.status_code >= 500:
            raise ParseError(f"产品检索失败（HTTP {resp.status_code}）")
        return parse_product_rows(resp.text), parse_rows_count(resp.text)

    def search_products(self, keyword: str = "", *, limit: int = 20) -> dict[str, Any]:
        rows, total = self._search_product_rows(keyword)
        size = max(1, min(int(limit or 20), 50))
        return {
            "keyword": keyword or "",
            "total": total,
            "fetched": len(rows),
            "count": min(len(rows), size),
            "truncated": bool((total or 0) > len(rows) or len(rows) > size),
            "products": [
                {
                    "product_id": row.product_id,
                    "code": row.code,
                    "name": row.name,
                    "category": row.category,
                    "unit": row.unit,
                    "spec": row.spec,
                }
                for row in rows[:size]
            ],
        }

    def find_product(self, keyword: str) -> ProductOption:
        rows, _ = self._search_product_rows(keyword)
        if not rows:
            raise ParseError(f"产品档案里找不到 {keyword!r}")
        exact = [r for r in rows if r.name.strip() == (keyword or "").strip()]
        if len(exact) == 1:
            return exact[0]
        if len(rows) == 1:
            return rows[0]
        names = "、".join(r.name for r in rows[:5])
        raise ParseError(
            f"产品 {keyword!r} 匹配到 {len(rows)} 条（{names}…），请给出更精确的产品名称或产品编码"
        )

    # ══════════════════ 5. 真正写入 ══════════════════
    def _sub_page_url(self, record_id: str, khid: str, discount: str | None, *,
                      cpid: str = "-1") -> str:
        qxid = self.auth.cfg.qxid_quote or self.auth.qxid_for(PAGE_PATH)
        uid = self.auth.user_id or ""
        zkl = (str(discount).strip() if discount not in (None, "") else "0") or "0"
        url = (f"/Admin/{SUB_PAGE_PATH}?PID={record_id}&ID={cpid}&ZKL={zkl}&QXID={qxid}"
               f"&SetPageID=872_1_&KHID={khid}&Modify=1&SFTZCP=0&DefaultCPDL=4")
        if uid:
            url += f"&LoginUserID={uid}"
        return f"{url}&time={int(time.time() * 1000)}"

    def _product_qty_readback(self, record_id: str, cpid: str) -> str | None:
        """打开产品行编辑页，读回该行的产品数量。

        数量在 ERP 里有两种存储：
          * 汇总值 `txtCPSL`（页面「产品数量：」框里算出来的那个）——校验与展示都用它；
          * 多款/多语言明细 `txtCPSL1..10`（对应明细行上的 `OpenInputCPSLZLData('<GUID>')`）。
        本工具只写汇总值，所以回读也优先看 `txtCPSL`。
        """
        try:
            html, _ = self.open_record(record_id)
        except ErpError:
            return None
        soup = soup_of(html)
        sub_url = self._sub_page_url(
            record_id,
            get_input_value(soup, "txtKHID") or "",
            get_input_value(soup, "txtZKL"),
            cpid=cpid,
        )
        resp = self.auth.client.get(sub_url)
        if resp.status_code != 200:
            return None
        page = soup_of(resp.text)
        for control in ("txtCPSL", "txtCPSL1"):
            value = (get_input_value(page, control) or "").strip()
            if value:
                return value
        return None

    def _restore_product_category(
        self, body: dict[str, str], editor_html: str, product_name: str
    ) -> None:
        """把树状「产品类别」补回提交体。

        `txtCPLB` 是 ComponentArt 树形下拉，显示文字由前端 JS 写进 `txtCPLB_Input`，
        服务端渲染出来的 HTML 里只有占位符。不改的话重新保存会被挡在
        「请选择【产品类别】的数据！」，哪怕这次只想改数量。
        """
        page = soup_of(editor_html)
        if not _is_placeholder(get_input_value(page, "txtCPLB_Input")):
            return
        if not product_name:
            return
        try:
            archive = self.find_product(product_name)
        except ErpError:
            return
        if "txtCPLB_Input" in body:
            body["txtCPLB_Input"] = archive.category
        archive_fields = archive.page_fields()
        for key in ("txtLBID", "txtLBBM"):
            if key in body and not (body.get(key) or "").strip():
                body[key] = archive_fields.get(key, "")

    def create(
        self,
        *,
        customer: str | None = None,
        sales: str | None = None,
        products: list[dict[str, Any]] | None = None,
        contact: str | None = None,
        phone: str | None = None,
        mobile: str | None = None,
        fax: str | None = None,
        discount: str | None = None,
        remark: str | None = None,
        product_memo: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """在 ERP 里真正新建一条报价需求（主表 + 1 条产品明细）。

        `confirm=False` 时只做校验与预览，不写库。
        """
        prepared = self.prepare(
            customer=customer, sales=sales, products=products, contact=contact,
            phone=phone, mobile=mobile, fax=fax, discount=discount,
            remark=remark, product_memo=product_memo,
        )
        if not prepared["ok"]:
            return {**prepared, "status": "invalid"}

        items = prepared["products"]
        if len(items) != 1:
            return {
                **prepared, "status": "unsupported",
                "message": "当前实现一次只支持 1 条产品明细；多条请分次调用。",
            }
        if not confirm:
            return {
                **prepared, "status": "needs_confirmation",
                "message": "校验已通过。确认真正写入 ERP 请传 confirm=true。",
            }

        header: dict[str, str] = dict(prepared["header"])
        khid = header.get("txtKHID", "")
        customer_info = prepared.get("customer") or {}
        steps: list[dict[str, Any]] = []

        # ---- 步骤 1：建主表（btn_SonNew 的副作用就是先保存主表）----
        html, page_url = self._open_form_html()
        body = form_snapshot(html)
        body.update(header)
        body.update({"__EVENTTARGET": "btn_SonNew", "__EVENTARGUMENT": "",
                     "__LASTFOCUS": ""})
        resp = self.auth.client.post(page_url, data=body)
        if resp.status_code >= 500:
            raise ParseError(f"新建主表失败（HTTP {resp.status_code}）")
        main_after = resp.text
        soup_after = soup_of(main_after)
        record_id = (get_input_value(soup_after, "lblID") or "").strip()
        bdbh = (get_input_value(soup_after, "txtBDBH") or "").strip()
        steps.append({"step": "create_header", "ok": bool(record_id),
                      "record_id": record_id, "bdbh": bdbh})
        if not record_id:
            message = _find_validation_message(main_after)
            return {
                "status": "rejected", "stage": "create_header",
                "message": message or "ERP 没有生成记录 ID（客户或业务员可能无效）",
                "steps": steps,
            }

        # ---- 步骤 2：产品明细子页 ----
        product = self.find_product(items[0]["name"])
        discount_value = discount or customer_info.get("discount") or ""
        sub_url = self._sub_page_url(record_id, khid, discount_value)
        sub_resp = self.auth.client.get(sub_url)
        if sub_resp.status_code != 200:
            raise ParseError(f"产品子页打开失败（HTTP {sub_resp.status_code}）")
        sub_html = sub_resp.text
        allowed = _control_names(sub_html)
        sub_body = form_snapshot(sub_html)
        for key, value in product.page_fields().items():
            if key in allowed:
                sub_body[key] = value
        unit_value = _select_value(sub_html, "txtDWMC", product.unit)
        if unit_value is not None and "txtDWMC" in allowed:
            sub_body["txtDWMC"] = unit_value
        # 产品数量：ERP 真正校验/落库的是隐藏字段 `txtCPSL`；
        # `txtCPSL1..10` 只是「每款一行」的展示输入框（款数=1 时取 txtCPSL1）。
        # 只填 txtCPSL1 会一直被挡在「【产品数量】不能为空！」，两者都要发。
        qty_text = _fmt_qty(items[0]["qty"])
        sub_body["txtCPSL1"] = qty_text
        if "txtCPSL" in allowed:
            sub_body["txtCPSL"] = qty_text
        sub_body["txtCPLB_Input"] = product.category
        for key, control in SUB_FIELD_MAP.items():
            raw = items[0].get(key)
            if raw in (None, ""):
                continue
            if control not in allowed:
                continue
            text = str(raw)
            resolved = _select_value(sub_html, control, text)
            sub_body[control] = resolved if resolved is not None else text
        sub_body["btnSave"] = "· 保存 ·"
        save_resp = self.auth.client.post(sub_url, data=sub_body)
        if save_resp.status_code >= 500:
            raise ParseError(f"产品明细保存失败（HTTP {save_resp.status_code}）")
        warning = _find_validation_message(save_resp.text)
        # 注意：ERP 保存成功后仍可能在返回页里残留一条提示（实测「请输入【产品数量】…」就是这种），
        # 所以**不能**用提示文案判断成败，必须以回读结果为准。
        steps.append({"step": "save_product", "ok": True,
                      "product_id": product.product_id, "message": warning or ""})

        # ---- 步骤 3：回读校验（不再重复保存主表）----
        # 血的教训：用 `btn_SonNew` 之前的旧快照再 POST 一次 btnSave，会把刚写入的
        # 产品明细整行覆盖掉。btn_SonNew 已经保存过主表，这里只做回读。
        detail_saved = False
        row_created = False
        qty_saved = False
        detail_error = ""
        expected_qty = _fmt_qty(items[0]["qty"])
        try:
            from ..quote_request import QuoteRequestTool

            check = QuoteRequestTool(self.auth).fetch_one(self.auth.client, record_id)
            row_created = bool((check.get("CPMC") or "").strip())
            # 数量不在单据页的明细格子里（那是多语言明细），要打开行编辑页读汇总值。
            rows = parse_detail_rows(self.open_record(record_id)[0])
            readback = self._product_qty_readback(record_id, rows[0]["cpid"]) if rows else None
            qty_saved = readback is not None and float_or_none(readback) == float_or_none(expected_qty)
            detail_saved = row_created and qty_saved
            if not row_created:
                detail_error = "产品明细行没有落库"
            elif not qty_saved:
                detail_error = (
                    f"明细行已建，但产品数量回读为 {readback!r}（期望 {expected_qty}）"
                )
        except ErpError as exc:  # noqa: BLE001
            detail_error = f"写后校验失败：{exc}"
        steps.append({
            "step": "verify", "ok": detail_saved,
            "row_created": row_created, "qty_saved": qty_saved,
            "message": detail_error,
        })

        status = "created" if detail_saved else ("partial" if row_created else "failed")
        return {
            "status": status,
            "ok": status == "created",
            "record_id": record_id,
            "bdbh": bdbh,
            "url": page_url,
            "header_saved": True,
            "detail_saved": detail_saved,
            "detail_error": detail_error,
            "product": {
                "name": product.name, "spec": product.spec,
                "unit": product.unit, "qty": items[0]["qty"],
            },
            "steps": steps,
            "message": (
                "主表与产品明细均已落库" if status == "created"
                else "主表已落库，但产品明细未保存成功；请到 ERP 里手工补一行，或等明细协议修复后重试"
            ),
        }


    # ══════════════════ 5c. 修改（先 btnModify 再 btnSave）══════════════════
    def open_record(self, record_id: str) -> tuple[str, str]:
        """以只读态打开已存在单据，返回 (html, url)。"""
        record_id = (record_id or "").strip()
        if not record_id:
            raise ParseError("缺少 record_id")
        url = self._record_url(record_id)
        resp = self.auth.client.get(url)
        if resp.status_code != 200:
            raise ParseError(f"单据打开失败（HTTP {resp.status_code}）")
        if not (get_input_value(soup_of(resp.text), "lblID") or "").strip():
            raise ParseError(f"记录 {record_id} 不存在或已被删除")
        return resp.text, url

    def list_details(self, record_id: str) -> dict[str, Any]:
        """列出一条单据的产品明细行（`cpid` 用于后续修改/删除该行）。"""
        html, _ = self.open_record(record_id)
        rows = parse_detail_rows(html)
        return {
            "record_id": record_id,
            "bdbh": (get_input_value(soup_of(html), "txtBDBH") or "").strip(),
            "count": len(rows),
            "details": rows,
        }

    def update_quote(
        self,
        record_id: str,
        *,
        remark: str | None = None,
        contact: str | None = None,
        phone: str | None = None,
        mobile: str | None = None,
        fax: str | None = None,
        discount: str | None = None,
        product_memo: str | None = None,
        sales: str | None = None,
        merchandiser: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """修改单据表头。

        协议：`GET Option=3` → `POST btnModify`（解锁 [保存]）→ `POST btnSave`。
        `confirm=False` 只计算将要改什么，不发任何 POST。
        """
        html, url = self.open_record(record_id)
        bdbh = (get_input_value(soup_of(html), "txtBDBH") or "").strip()
        allowed = _control_names(html)

        changes: dict[str, str] = {}
        for value, control in (
            (remark, "txtRemark"), (contact, "txtLXR"), (phone, "txtLXDH"),
            (mobile, "txtSJ"), (fax, "txtCZ"), (discount, "txtZKL"),
            (product_memo, "txtCPMX"),
        ):
            if value is not None and control in allowed:
                changes[control] = str(value)

        for label, control, name in (
            (sales, "txtYGXM", "业务员"), (merchandiser, "txtGDYXM", "跟单员"),
        ):
            if label is None:
                continue
            if control not in allowed:
                raise ParseError(f"该单据页面上没有 {name} 下拉（{control}）")
            resolved = _select_value(html, control, label)
            if resolved is None or resolved == "-0-":
                raise ParseError(f"{name} {label!r} 不在该单据的下拉选项里")
            changes[control] = resolved

        if not changes:
            return {
                "status": "no_changes", "ok": False, "record_id": record_id,
                "bdbh": bdbh, "message": "没有提供任何要修改的字段",
            }

        if not confirm:
            return {
                "status": "needs_confirmation", "ok": False, "record_id": record_id,
                "bdbh": bdbh, "changes": changes,
                "message": "确认修改请传 confirm=true；会先 btnModify 再 btnSave。",
            }

        # 1) 进入修改态
        body = form_snapshot(html)
        body.update({"__EVENTTARGET": "btnModify", "__EVENTARGUMENT": "",
                     "__LASTFOCUS": ""})
        edited = self.auth.client.post(url, data=body)
        if edited.status_code >= 500:
            raise ParseError(f"进入修改态失败（HTTP {edited.status_code}）")
        if 'id="btnSave" disabled' in edited.text:
            return {
                "status": "rejected", "ok": False, "record_id": record_id,
                "bdbh": bdbh, "changes": changes,
                "message": "btnModify 之后 [保存] 仍是 disabled —— 可能没有修改权限或单据已审核",
            }

        # 2) 保存
        body2 = form_snapshot(edited.text)
        body2.update(changes)
        body2.update({"btnSave": "", "__EVENTTARGET": "", "__EVENTARGUMENT": "",
                      "__LASTFOCUS": ""})
        saved = self.auth.client.post(url, data=body2)
        if saved.status_code >= 500:
            raise ParseError(f"保存失败（HTTP {saved.status_code}）")

        # 3) 回读复核
        verify_html, _ = self.open_record(record_id)
        verified = {control: _read_value(verify_html, control) for control in changes}
        ok = all(str(changes[c]) == str(verified.get(c, "")) for c in changes)
        return {
            "status": "updated" if ok else "failed",
            "ok": ok,
            "record_id": record_id,
            "bdbh": bdbh,
            "changes": changes,
            "verified": verified,
            "message": "已保存并复核一致" if ok else "保存后回读不一致，请人工确认",
        }

    def update_product(
        self,
        record_id: str,
        cpid: str,
        *,
        name: str | None = None,
        spec: str | None = None,
        qty: Any = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """修改某条产品明细（点产品名称打开的那个编辑页）。

        协议：`GET Add_CPZL.aspx?PID=<单据>&ID=<CPID>&...` → `POST btnSave`。
        保存后回读明细行名称做复核；不一致时返回 `failed`（不假报成功）。
        """
        cpid = (cpid or "").strip()
        if not cpid:
            raise ParseError("缺少 cpid（可先用 list_details 查）")
        html, _ = self.open_record(record_id)
        details = parse_detail_rows(html)
        target = next((row for row in details if row["cpid"] == cpid), None)
        if target is None:
            raise ParseError(f"记录 {record_id} 里没有明细行 {cpid}")

        changes: dict[str, str] = {}
        if name is not None:
            changes["txtCPMC"] = str(name)
        if spec is not None:
            changes["txtGG"] = str(spec)
        if qty is not None:
            changes["txtCPSL1"] = _fmt_qty(qty)
            changes["txtCPSL"] = _fmt_qty(qty)
        if not changes:
            return {"status": "no_changes", "ok": False, "cpid": cpid,
                    "message": "没有提供任何要修改的产品字段"}

        if not confirm:
            return {
                "status": "needs_confirmation", "ok": False,
                "record_id": record_id, "cpid": cpid,
                "current": target, "changes": changes,
                "message": "确认修改产品请传 confirm=true。",
            }

        soup = soup_of(html)
        sub_url = self._sub_page_url(
            record_id,
            get_input_value(soup, "txtKHID") or "",
            get_input_value(soup, "txtZKL"),
            cpid=cpid,
        )
        editor = self.auth.client.get(sub_url)
        if editor.status_code != 200:
            raise ParseError(f"产品编辑页打开失败（HTTP {editor.status_code}）")
        body = form_snapshot(editor.text)
        body.update(changes)
        self._restore_product_category(body, editor.text, target.get("name", ""))
        body["btnSave"] = "· 保存 ·"
        saved = self.auth.client.post(sub_url, data=body)
        if saved.status_code >= 500:
            raise ParseError(f"产品保存失败（HTTP {saved.status_code}）")

        after = self.list_details(record_id)
        row = next((r for r in after["details"] if r["cpid"] == cpid), None)
        verified_name = (row or {}).get("name", "")
        name_ok = name is None or verified_name.strip() == str(name).strip()
        verified_qty = self._product_qty_readback(record_id, cpid) if qty is not None else None
        qty_ok = qty is None or float_or_none(verified_qty) == float_or_none(_fmt_qty(qty))
        ok = bool(name_ok and qty_ok)
        return {
            "status": "updated" if ok else "failed",
            "ok": ok,
            "record_id": record_id,
            "cpid": cpid,
            "changes": changes,
            "verified_name": verified_name,
            "verified_qty": verified_qty,
            "message": "已保存" if ok else "保存后回读与预期不一致，产品保存协议还没完全跑通",
        }

    # ══════════════════ 6. 删除单据 ══════════════════
    def _record_url(self, record_id: str) -> str:
        """打开已存在单据：`Option=3` 是只读打开，`btnDelete` 在这状态下可用。"""
        uid = self.auth.user_id or ""
        url = f"/Admin/{PAGE_PATH}?id={record_id}&Option=3&QXID=876&MenuTree=1"
        return f"{url}&LoginUserID={uid}" if uid else url

    def delete_quote(self, record_id: str, *, confirm: bool = False) -> dict[str, Any]:
        """删除一条报价需求单。

        协议：打开 `Add.aspx?id=<GUID>&Option=3` → `POST __EVENTTARGET=btnDelete`。
        删除不可恢复，所以默认 `confirm=False` 只做存在性检查。
        """
        record_id = (record_id or "").strip()
        if not record_id:
            raise ParseError("缺少 record_id")

        url = self._record_url(record_id)
        resp = self.auth.client.get(url)
        if resp.status_code != 200:
            raise ParseError(f"单据打开失败（HTTP {resp.status_code}）")

        soup = soup_of(resp.text)
        bdbh = (get_input_value(soup, "txtBDBH") or "").strip()
        current_id = (get_input_value(soup, "lblID") or "").strip()
        if not current_id:
            return {
                "status": "not_found", "ok": False, "record_id": record_id,
                "message": "该记录不存在或已被删除",
            }

        if not confirm:
            return {
                "status": "needs_confirmation", "ok": False,
                "record_id": record_id, "bdbh": bdbh,
                "message": "确认删除请传 confirm=true；删除不可恢复。",
            }

        body = form_snapshot(resp.text)
        body.update({"__EVENTTARGET": "btnDelete", "__EVENTARGUMENT": "",
                     "__LASTFOCUS": ""})
        posted = self.auth.client.post(url, data=body)
        if posted.status_code >= 500:
            raise ParseError(f"删除请求失败（HTTP {posted.status_code}）")

        # 复核：再打开一次，lblID 为空才算真的删掉
        check = self.auth.client.get(url)
        still_there = bool((get_input_value(soup_of(check.text), "lblID") or "").strip())
        return {
            "status": "failed" if still_there else "deleted",
            "ok": not still_there,
            "record_id": record_id,
            "bdbh": bdbh,
            "message": ("删除后仍能打开该记录，请人工确认" if still_there else "已删除"),
        }


__all__ = [
    "CUSTOMER_PICKER_PATH", "CUSTOMER_TO_HEADER", "PAGE_PATH", "PRODUCT_PICKER_PATH",
    "QuoteCreateService", "REQUIRED_INPUTS", "SELECT_LABELS", "SUB_PAGE_PATH",
]
