"""ASP.NET 表单工具测试。"""

from erp_tools.core.aspnet import (
    build_postback,
    delta_update_panel,
    extract_viewstate,
    soup_of,
)


def test_extract_viewstate():
    html = """
    <form>
      <input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="AAA" />
      <input type="hidden" name="__VIEWSTATEGENERATOR" value="BBB" />
      <input type="text" name="txtKey" value="x" />
    </form>
    """
    vs = extract_viewstate(html)
    assert vs["__VIEWSTATE"] == "AAA"
    assert vs["__VIEWSTATEGENERATOR"] == "BBB"
    assert "__EVENTVALIDATION" not in vs


def test_build_postback_sets_targets():
    base = {"txtYF": "2026-11", "__EVENTTARGET": "stale", "__ASYNCPOST": "true"}
    body = build_postback(base, {"__VIEWSTATE": "V"}, event_target="btnNext",
                          strip_keys=("__ASYNCPOST",))
    assert body["__EVENTTARGET"] == "btnNext"
    assert body["ScriptManager1"] == "UpdatePanel1|btnNext"
    assert body["__VIEWSTATE"] == "V"
    assert body["txtYF"] == "2026-11"
    assert "__ASYNCPOST" not in body


def test_delta_update_panel_extracts_fragment():
    delta = (
        "1|#||2|1234|3|SCRIPT|4|/x.js|"
        "updatePanel|UpdatePanel1|<div id=\"gViewDiv2\"><span id=\"lblRowsCount\">7</span></div>"
        "|hiddenField|__VIEWSTATE|ENC|"
    )
    frag = delta_update_panel(delta)
    assert "lblRowsCount" in frag
    assert "__VIEWSTATE" not in frag


def test_delta_returns_full_html_untouched():
    html = "<!DOCTYPE html><html><body>ok</body></html>"
    assert delta_update_panel(html) == html


def test_soup_of_handles_broken_html():
    soup = soup_of("<table><tr><td>a</td></tr>")
    assert soup.find("td").get_text() == "a"
