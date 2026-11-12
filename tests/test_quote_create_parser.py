"""`tools/quote_create`：新增报价需求页的解析与提交体组装（全离线）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from erp_tools.core.errors import ParseError
from erp_tools.tools.quote_create import (
    CUSTOMER_FIELDS,
    CustomerOption,
    QuoteCreateService,
    clear_options_cache,
    parse_customer_rows,
    parse_detail_rows,
    parse_new_form,
    parse_rows_count,
)

SALES_GUID = "11111111-1111-1111-1111-111111111111"
MERCHANDISER_GUID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def _clean_options_cache():
    clear_options_cache()
    yield
    clear_options_cache()


# ══════════════════ 空白页 ══════════════════
def test_parse_new_form_fields(fixture_text) -> None:
    form = parse_new_form(fixture_text("quote_new_page.html"), url="/x")

    assert form.url == "/x"
    assert form.fields["txtBDBH"] == ""
    assert form.fields["lblQXBH"] == "873"
    assert "txtBDBH" in form.readonly
    assert "txtKHMC" in form.readonly
    assert "txtZDR" in form.readonly          # disabled 也算只读
    assert "txtZKL" not in form.readonly

    # 提交类按钮不应进入表单快照
    assert "KHMC_Select" not in form.fields

    assert set(form.textareas) == {"txtRemark", "txtCPMX"}


def test_parse_new_form_selects(fixture_text) -> None:
    form = parse_new_form(fixture_text("quote_new_page.html"))

    assert set(form.selects) == {"txtYGXM", "txtGDYXM", "txtDJTX"}
    assert form.selects["txtYGXM"]["张三"] == SALES_GUID
    assert form.selects["txtGDYXM"]["李四"] == MERCHANDISER_GUID
    assert form.selects["txtDJTX"]["II级(标准)"] == "02"

    assert form.resolve_select("txtYGXM", "张三") == SALES_GUID
    assert form.resolve_select("txtYGXM", SALES_GUID) == SALES_GUID
    assert form.resolve_select("txtYGXM", "不存在的人") is None
    assert form.resolve_select("txtYGXM", "") is None


# ══════════════════ 客户选择页 ══════════════════
def test_parse_customer_rows(fixture_text) -> None:
    rows = parse_customer_rows(fixture_text("customer_picker_page.html"))

    assert len(rows) == 2, "空占位行必须被丢弃"
    first = rows[0]
    assert first.get("customer_id") == "aaaaaaaa-0000-0000-0000-000000000001"
    assert first.get("customer_code") == "03.0064"
    assert first.get("customer_name") == "示例客户A"
    assert first.get("sales") == "张三"
    assert first.get("sales_id") == SALES_GUID
    assert first.get("merchandiser") == "李四"
    assert first.get("discount") == "10"
    assert first.get("short_name") == "示例A"

    second = rows[1]
    assert second.get("customer_name") == "示例客户B"
    assert second.get("currency") == "USD"


def test_customer_field_positions_match_definition() -> None:
    """位置含义一旦错位，取到的客户 ID 就会是别的字段——这里钉死。"""
    assert CUSTOMER_FIELDS[0] == "customer_id"
    assert CUSTOMER_FIELDS[1] == "customer_code"
    assert CUSTOMER_FIELDS[2] == "customer_name"
    assert CUSTOMER_FIELDS[14] == "discount"
    assert CUSTOMER_FIELDS[18] == "sales_id"
    assert CUSTOMER_FIELDS[34] == "short_name"
    assert len(CUSTOMER_FIELDS) == 39


def test_parse_rows_count(fixture_text) -> None:
    assert parse_rows_count(fixture_text("customer_picker_page.html")) == 2
    assert parse_rows_count("<html></html>") is None


def test_empty_placeholder_rows_are_ignored(fixture_text) -> None:
    html = """<table><tr ondblclick="SelectKHZL('','','', ...)"></tr></table>"""
    assert parse_customer_rows(html) == []


# ══════════════════ 提交体组装（离线，不发请求）══════════════════
class _StubService(QuoteCreateService):
    def __init__(self, form_html: str, customer: CustomerOption | None,
                 username: str = "tester") -> None:
        super().__init__(auth=SimpleNamespace(cfg=SimpleNamespace(username=username)))  # type: ignore[arg-type]
        self._form_html = form_html
        self._customer = customer
        self.open_calls = 0

    def _page_url(self) -> str:  # noqa: D102
        return "/stub/add.aspx"

    def open_form(self):  # noqa: D102
        self.open_calls += 1
        return parse_new_form(self._form_html)

    def find_customer(self, keyword: str):  # noqa: D102
        if self._customer is None:
            raise ParseError(f"找不到客户 {keyword!r}")
        return self._customer


def _customer() -> CustomerOption:
    return CustomerOption(values={
        "customer_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "customer_code": "03.0064",
        "customer_name": "示例客户A",
        "sales": "张三",
        "sales_id": SALES_GUID,
        "merchandiser": "李四",
        "price_system_id": "02",
        "discount": "10",
        "contact": "张三",
        "phone": "12345678",
    })


def test_prepare_fills_header_from_customer(fixture_text) -> None:
    svc = _StubService(fixture_text("quote_new_page.html"), _customer())

    result = svc.prepare(
        customer="示例客户A",
        products=[{"name": "彩盒", "spec": "210*148", "qty": 5000}],
    )

    assert result["ok"] is True, result["errors"]
    assert result["errors"] == []
    header = result["header"]
    assert header["txtKHID"] == "aaaaaaaa-0000-0000-0000-000000000001"
    assert header["txtKHMC"] == "示例客户A"
    assert header["txtYGXM"] == SALES_GUID
    assert header["txtGDYXM"] == MERCHANDISER_GUID
    assert header["txtDJTX"] == "02"
    assert header["txtZKL"] == "10"

    assert result["products"][0] == {
        "name": "彩盒", "spec": "210*148", "qty": 5000.0, "fields": {},
    }

    # 写入已开放（erp_quote_create），但 prepare 本身仍然不落库
    assert result["submit_supported"] is True
    assert "confirm=true" in result["next"]


def test_prepare_reports_validation_errors(fixture_text) -> None:
    svc = _StubService(fixture_text("quote_new_page.html"), _customer())

    result = svc.prepare(customer=None, products=[])

    assert result["ok"] is False
    joined = " ".join(result["errors"])
    assert "customer" in joined
    assert "products" in joined


def test_prepare_rejects_bad_qty_and_spec(fixture_text) -> None:
    svc = _StubService(fixture_text("quote_new_page.html"), _customer())

    result = svc.prepare(
        customer="示例客户A",
        products=[{"name": "彩盒", "spec": "", "qty": -1}],
    )

    assert result["ok"] is False
    joined = " ".join(result["errors"])
    assert "spec" in joined
    assert "qty" in joined


def test_prepare_rejects_unknown_sales(fixture_text) -> None:
    svc = _StubService(fixture_text("quote_new_page.html"), _customer())

    result = svc.prepare(
        customer="示例客户A",
        sales="查无此人",
        products=[{"name": "彩盒", "spec": "210*148", "qty": 100}],
    )

    assert result["ok"] is False
    assert any("业务员" in e for e in result["errors"])


def test_prepare_only_keeps_fields_present_on_page(fixture_text) -> None:
    """页面没有的控件绝不能提交，否则 ASP.NET 会忽略或报错。"""
    svc = _StubService(fixture_text("quote_new_page.html"), _customer())

    result = svc.prepare(
        customer="示例客户A",
        products=[{"name": "彩盒", "spec": "210*148", "qty": 100}],
        remark="客户要求加急",
        product_memo="四色印刷",
    )

    page_fields = parse_new_form(fixture_text("quote_new_page.html")).fields
    assert set(result["header"]) <= set(page_fields)
    assert result["header"]["txtRemark"] == "客户要求加急"
    assert result["header"]["txtCPMX"] == "四色印刷"


def test_prepare_warns_when_erp_required_fields_missing(fixture_text) -> None:
    """ERP 必填链缺字段时要提前提醒——否则明细被拒时主表已经建好了。"""
    svc = _StubService(fixture_text("quote_new_page.html"), _customer())

    result = svc.prepare(
        customer="示例客户A",
        products=[{"name": "彩盒", "spec": "210*148", "qty": 100,
                   "packing": "纸箱包装"}],
    )

    assert result["ok"] is True          # 只是 warning，不拦
    joined = " ".join(result["warnings"])
    assert "ERP 必填字段" in joined
    assert "packing" not in joined       # 已经给了
    for key in ("pallet", "delivery", "product_desc", "inspection"):
        assert key in joined, f"{key} 应该被提示为缺失"


def test_prepare_no_required_warning_when_all_filled(fixture_text) -> None:
    svc = _StubService(fixture_text("quote_new_page.html"), _customer())

    result = svc.prepare(
        customer="示例客户A",
        products=[{
            "name": "彩盒", "spec": "210*148", "qty": 100,
            "binding": "胶装/PAD书", "packing": "纸箱包装", "pallet": "不打卡板",
            "delivery": "FOB(NGV)", "delivery_date": "2026-12-20",
            "expected_date": "2026-12-01", "parts_desc": "无",
            "attribute": "三岁以上", "product_type": "新产品",
            "product_desc": "示例", "modify_log": "无", "inspection": "无",
            "cases": 1, "languages": 1,
        }],
    )

    assert result["ok"] is True
    assert not any("ERP 必填字段" in w for w in result["warnings"])


# ══════════════════ 下拉选项（业务员 / 跟单员）══════════════════
def test_list_options_builds_fields_and_employee_union(fixture_text) -> None:
    svc = _StubService(fixture_text("quote_new_page.html"), _customer())

    result = svc.list_options()

    assert set(result["fields"]) == {"sales", "merchandiser", "price_system"}

    sales = result["fields"]["sales"]
    assert sales["label"] == "业务员"
    assert sales["page_field"] == "txtYGXM"
    assert sales["count"] == 2
    assert [o["name"] for o in sales["options"]] == ["张三", "王五"]
    assert sales["options"][0]["id"] == SALES_GUID
    # 占位项必须被剔除
    assert all(o["name"] != "<--请选择-->" for o in sales["options"])
    assert all(o["id"] != "-0-" for o in sales["options"])

    assert result["fields"]["merchandiser"]["count"] == 1
    assert result["fields"]["merchandiser"]["options"][0]["id"] == MERCHANDISER_GUID
    assert result["fields"]["price_system"]["count"] == 2

    employees = {e["name"]: e for e in result["employees"]}
    assert employees["张三"]["can_sales"] is True
    assert employees["张三"]["can_merchandiser"] is False
    assert employees["李四"]["can_merchandiser"] is True
    assert employees["李四"]["can_sales"] is False

    assert result["cached"] is False
    assert result["as_of"]
    assert result["notes"]


def test_list_options_filter_by_chinese_alias(fixture_text) -> None:
    svc = _StubService(fixture_text("quote_new_page.html"), _customer())

    result = svc.list_options("跟单员")

    assert result["filtered_by"] == "txtGDYXM"
    assert set(result["fields"]) == {"merchandiser"}
    assert result["fields"]["merchandiser"]["count"] == 1
    # employees 仍然完整返回，便于一次回答「谁能跟单、谁能做业务」
    assert len(result["employees"]) == 3


def test_list_options_filter_by_english_alias(fixture_text) -> None:
    svc = _StubService(fixture_text("quote_new_page.html"), _customer())
    result = svc.list_options("sales")
    assert set(result["fields"]) == {"sales"}


def test_list_options_rejects_unknown_field(fixture_text) -> None:
    svc = _StubService(fixture_text("quote_new_page.html"), _customer())
    with pytest.raises(ParseError) as excinfo:
        svc.list_options("仓库")
    assert "merchandiser" in str(excinfo.value)


def test_list_options_caches_within_ttl(fixture_text) -> None:
    svc = _StubService(fixture_text("quote_new_page.html"), _customer())

    first = svc.list_options()
    assert first["cached"] is False
    assert svc.open_calls == 1

    second = svc.list_options()
    assert second["cached"] is True
    assert svc.open_calls == 1, "TTL 内不应重新抓页面"
    assert second["fields"]["sales"]["count"] == first["fields"]["sales"]["count"]

    refreshed = svc.list_options(refresh=True)
    assert refreshed["cached"] is False
    assert svc.open_calls == 2


def test_list_options_cache_is_per_account(fixture_text) -> None:
    a = _StubService(fixture_text("quote_new_page.html"), _customer(), username="u1")
    b = _StubService(fixture_text("quote_new_page.html"), _customer(), username="u2")

    a.list_options()
    result_b = b.list_options()

    assert result_b["cached"] is False, "不同账号的权限不同，不能共用缓存"
    assert b.open_calls == 1


# ══════════════════ 删除单据 ══════════════════
class _Resp:
    def __init__(self, text: str, status: int = 200) -> None:
        self.status_code = status
        self.text = text
        self.headers: dict[str, str] = {}


class _FakeClient:
    def __init__(self, html: str) -> None:
        self.html = html
        self.posts: list[tuple[str, dict | None]] = []

    def get(self, url: str) -> _Resp:  # noqa: D102
        return _Resp(self.html)

    def post(self, url: str, data=None) -> _Resp:  # noqa: D102
        self.posts.append((url, data))
        return _Resp(self.html)


_EXISTING_RECORD = (
    '<form id="form1">'
    '<input type="hidden" name="__VIEWSTATE" value="VS" />'
    '<input type="hidden" name="lblID" value="aaaaaaaa-0000-0000-0000-000000000001" />'
    '<input type="hidden" name="txtBDBH" value="BJXQ261111006" />'
    '</form>'
)
_BLANK_PAGE = '<form id="form1"><input type="hidden" name="lblID" value="" /></form>'


def _delete_service(html: str) -> tuple[QuoteCreateService, _FakeClient]:
    client = _FakeClient(html)
    auth = SimpleNamespace(user_id="2474", client=client)
    return QuoteCreateService(auth), client  # type: ignore[arg-type]


def test_delete_quote_requires_confirm() -> None:
    svc, client = _delete_service(_EXISTING_RECORD)

    result = svc.delete_quote("aaaaaaaa-0000-0000-0000-000000000001", confirm=False)

    assert result["status"] == "needs_confirmation"
    assert result["bdbh"] == "BJXQ261111006"
    assert client.posts == [], "confirm=False 绝不能发 POST"


def test_delete_quote_posts_btnDelete() -> None:
    svc, client = _delete_service(_EXISTING_RECORD)

    result = svc.delete_quote("aaaaaaaa-0000-0000-0000-000000000001", confirm=True)

    assert len(client.posts) == 1
    url, data = client.posts[0]
    assert "Option=3" in url and "id=aaaaaaaa" in url
    assert data["__EVENTTARGET"] == "btnDelete"
    # 假客户端每次都返回同一页，所以复核会认为没删掉 → failed，但必须诚实报告
    assert result["status"] == "failed"
    assert result["ok"] is False


def test_delete_quote_reports_missing_record() -> None:
    svc, client = _delete_service(_BLANK_PAGE)

    result = svc.delete_quote("aaaaaaaa-0000-0000-0000-000000000001", confirm=True)

    assert result["status"] == "not_found"
    assert client.posts == [], "记录不存在时不应发删除请求"


def test_delete_quote_rejects_empty_id() -> None:
    svc, _ = _delete_service(_EXISTING_RECORD)
    with pytest.raises(ParseError):
        svc.delete_quote("  ", confirm=True)


# ══════════════════ 明细行解析 / 修改 ══════════════════
_DETAIL_HTML = """
<table><tr><td id="tdCPZLLIst">
  <span onclick="OpenInputCPZLData('cpid-1','pid-1','0')">产品甲</span>
  <span onclick="OpenInputCPZLData('cpid-1','pid-1','1')">【删除】</span>
  <span onclick="OpenInputCPZLData('cpid-2','pid-1','0')">产品乙</span>
</td></tr></table>
"""


def test_parse_detail_rows_reads_cpid_and_name() -> None:
    rows = parse_detail_rows(_DETAIL_HTML)

    assert rows == [
        {"cpid": "cpid-1", "pid": "pid-1", "name": "产品甲"},
        {"cpid": "cpid-2", "pid": "pid-1", "name": "产品乙"},
    ], "删除按钮（OptionStr=1）不应被当成明细行"


_RECORD_PAGE = (
    '<form id="form1">'
    '<input type="hidden" name="__VIEWSTATE" value="VS" />'
    '<input type="hidden" name="lblID" value="aaaaaaaa-0000-0000-0000-000000000001" />'
    '<input type="hidden" name="txtBDBH" value="BJXQ261111011" />'
    '<textarea name="txtRemark">旧备注</textarea>'
    '<input name="txtLXR" value="" />'
    '<select name="txtYGXM"><option value="-0-">&lt;--请选择--&gt;</option>'
    '<option value="guid-chenchun">陈纯</option></select>'
    '</form>'
)


def test_update_quote_dry_run_makes_no_post() -> None:
    client = _FakeClient(_RECORD_PAGE)
    svc = QuoteCreateService(SimpleNamespace(user_id="2474", client=client))  # type: ignore[arg-type]

    result = svc.update_quote("aaaaaaaa-0000-0000-0000-000000000001",
                              remark="新备注", sales="陈纯", confirm=False)

    assert result["status"] == "needs_confirmation"
    assert result["bdbh"] == "BJXQ261111011"
    assert result["changes"] == {"txtRemark": "新备注", "txtYGXM": "guid-chenchun"}
    assert client.posts == [], "confirm=False 不能发任何 POST"


def test_update_quote_rejects_unknown_sales() -> None:
    client = _FakeClient(_RECORD_PAGE)
    svc = QuoteCreateService(SimpleNamespace(user_id="2474", client=client))  # type: ignore[arg-type]

    with pytest.raises(ParseError) as excinfo:
        svc.update_quote("aaaaaaaa-0000-0000-0000-000000000001", sales="查无此人")
    assert "业务员" in str(excinfo.value)


def test_update_quote_without_changes() -> None:
    client = _FakeClient(_RECORD_PAGE)
    svc = QuoteCreateService(SimpleNamespace(user_id="2474", client=client))  # type: ignore[arg-type]

    result = svc.update_quote("aaaaaaaa-0000-0000-0000-000000000001")

    assert result["status"] == "no_changes"
    assert client.posts == []
