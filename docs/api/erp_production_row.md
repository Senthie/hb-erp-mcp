# `erp_production_row` — 生产施工单**列表行内点击事件**

> 页面：列表页 `ShengChan/SC_SCSGD/Index.aspx`（QXID=740）每一行上的图标 / 链接
> CLI：`erp production-row --id <记录ID> --action <动作>`｜**只读**

列表页的行不是只有「双击打开详情」——每一行还挂着一串点击事件。本工具把它们
提取成可调用的动作，返回对应子页的**表格 + 链接 + 文本域**。

实测 2026-11-23（账号 00469 / UserID 1662）。

## 和 `erp_production_node` 的分工

| | 入口 | 打开方式 | 例子 |
| --- | --- | --- | --- |
| `erp_production_node` | **详情页**里的弹窗按钮 | 写 `lblSelect*` → 回发 `btnSelect*` → 取弹窗 URL | 产品资料、部件工序、装订D10 |
| `erp_production_row` | **列表页**每一行上的图标/链接 | 用行里的 `产品ID` 直接拼 GET 直链 | 电子稿袋、跟进信息、材料清单、打印 |

## 参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `record_id` | str | 施工单记录 ID（`erp_production_list` 的 `记录ID`） |
| `number` | str | 生产单号（与 `record_id` 二选一，内部会先按单号反查） |
| `action` | str | 动作名；`actions`（默认）= 列出这一行能点什么 |
| `product_id` | str | 产品行 ID；不传就用列表行里的 `产品ID` |
| `max_rows` | int | 每个表格最多返回多少行（默认 100，上限 500） |

## 动作清单

| action | 名称 | 目标页 | 状态 |
| --- | --- | --- | --- |
| `esign` | 电子稿袋（文件浏览） | `SC_SCSGD/WJML_Brow.aspx` | ✅ 可读（见下方限制） |
| `gjqk` | 产品跟进信息 | `BaoJia/BJ_KHBJXQ/KHBJXQ_CPGJXX.aspx` | ✅ 网格 `gridView3`（日期/跟进类型/制版单号/版次/跟进内容） |
| `zhzy` | 做货指引 | `BaoJia/BJ_KHBJXQ/KHBJXQ_CPZHZY.aspx` | ✅ 但页面**极大**（实测 26 MB / 1.6 万行） |
| `wjqk` | 文件跟进情况 | `BaoJia/BJ_KHBJXQ/Add_WJQK.aspx` | ✅ 需 `Modify=1&QXID=1533` |
| `zszm` | 专色油墨 | `SC_SCSGD/Add_D1_D16.aspx` | ✅ |
| `clqd` | 临时材料清单 | `ShengChan/SC_CPCLQD/Add.aspx` | ✅ QXID=3029，含主表 + 明细网格 |
| `print` | 打印报表列表 | `Report/ReportList.aspx` | ✅ QXBH=736，列出可打印报表名 |
| `info` | 行「信息」 | — | ❌ 无独立页（面板内 5 个按钮逐个回发） |
| `bjremark` | 部件备注 | `SC_SCSGD/Show_BJRemark.aspx` | ❌ ERP 侧 500（浏览器同样打不开） |
| `cphj` | 核价否 | — | ❌ 回发不产生弹窗，只回写单元格 |

中文别名都认：`电子稿袋` / `跟进信息` / `做货指引` / `文件状态` / `油墨` / `材料清单` / `打印` …

## 为什么有的动作走 GET、有的走回发

列表页的 JS 里，每个行内事件都是「写隐藏字段 → 点某个隐藏按钮」：

```js
function OpenDirFileList(PID, CPID) { InputData("lblID", PID); InputData("lblCPID", CPID); $HB("BtnDirFileList").click(); }
function OpenSelectWJQK(CPID)      { $HB("lblCPID").value = CPID; $HB("btn_WJQK").click(); }
function IndexPrint(PID)           { InputData("lblID", PID); $HB("btnIndexPrint").click(); }
function LinCLQD(ID, QXID, RQXID)  { window.location.href = "../../ShengChan/SC_CPCLQD/Add.aspx?id=" + ID + ... }
```

回发后服务端只是回一个 `window.open('...aspx?...')`。实测发现**回发并不可靠**：
`btn_WJQK` / `btnGJQK` 会忽略我们传的 `lblCPID`，改用页面自己的选中行上下文
（拿到的是别的产品行）。所以这里改成**直接用行里的 `产品ID` 拼 GET 直链**——
参数完全可控，也不需要两次往返。

例外是 `bjremark`：它的弹窗 URL 是对的（`Show_BJRemark.aspx?CPID=&PID=`），
但该页本身报 `NullReferenceException` → 记为不可读。

## 返回

```json
{
  "action": "gjqk",
  "action_label": "产品跟进信息",
  "record_id": "30a7b30c-…",
  "number": "SGD2611230005",
  "product_id": "204731AE-…",
  "url": "/Admin/BaoJia/BJ_KHBJXQ/KHBJXQ_CPGJXX.aspx?ID=204731AE-…&LoginUserID=1662",
  "title": "【产品跟进信息】",
  "tables": [
    {"id": "gridView3",
     "headers": ["序号", "日期", "跟进类型", "制版单号", "版次", "跟进内容"],
     "rows": [{"序号": "1", "日期": "2026-11-20", "跟进类型": "…"}],
     "count": 3, "truncated": false}
  ],
  "links": [{"text": "刀线图", "href": "/Admin/FuJian/…"}],
  "texts": {},
  "size": 12810
}
```

`action=actions` 返回这一行的动作清单，每项带 `usable` / `page` / `url` /
`on_this_row`（这一行**实际**挂没挂这个事件）以及不可用时的 `note`。

### 电子稿袋的限制

文件清单不在页面里，而是 JSONP 取 `{lblElecbagUrl}/Ajax/ElecbagOption.ashx?FunName=GetFileList`
（7 个类别：产品制作说明 / 大版 / 箱唛 / 刀线 / 修改 / EVA胶水模 / 扫描检查报表）。
**这套 ERP 的 `lblElecbagUrl` 是空的**（没配稿袋文件服务器），所以只能返回
`categories`，`files` 为空，并附 `note` 说明；ERP 网页上点这些类别同样是空列表。
配置好后本工具会自动逐类别取回文件清单。

## CLI

```powershell
erp production-row --id <记录ID>                       # 列出这一行能点什么
erp production-row --id <记录ID> --action 材料清单
erp production-row --id <记录ID> --action print --out out\report.json
```

## 注意

- `record_id` 必须在**列表查询的日期区间内**才能定位到行（默认本月 1 号 ~ 今天）；
  区间外请先用 `erp_production_list` 指定 `from_date/to_date`。
- `zhzy`（做货指引）页面可能几十 MB，用于 LLM 时建议先只取 `url`，或配合 `max_rows` 截断。
- 所有动作都是**只读**；没有任何写操作（反馈/审核/删除都未接入）。
