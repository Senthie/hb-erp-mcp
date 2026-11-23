# `erp_quote_attachment` — 报价需求附件（列表 / 上传 / 下载）

> 菜单：**业务报价管理 › 业务需求 › 客户报价需求**
> 页面：`BaoJia/BJ_KHBJXQ/BJ_KHBJXQ_FJ.aspx`｜CLI：`erp quote-attachment`｜**读 + 写**

## 用途

新增/编辑报价需求页上有个 **`[附件(无)]` / `[附件(有)]`** 链接（`<a id="btnFJ">`），
点开就是「上传附件」页。本接口把它抽象出来，支持：

1. **列出现有附件**（名称/描述/备注/排序号/文件ID）
2. **上传附件**——AI 可以直接把 stream 传上来（base64 / 纯文本 / 本机路径）
3. **下载附件**——回传 base64，或另存到本机目录（CLI）

`erp_quote_create` 的 `attachments` 参数内部就是调这套东西，所以「建单 + 传附件」一次调用就能完成。

## 页面协议（实测 2026-11-23，账号 15627）

```
新增页 Add.aspx   <a id="btnFJ" href="javascript:__doPostBack('btnFJ','')">[附件(无)]</a>
   │  回发 btnFJ
   └─ window.open('http://<host>:8887/Admin/BaoJia/BJ_KHBJXQ/BJ_KHBJXQ_FJ.aspx?PID=<记录ID>&Modify=0')

附件页 BJ_KHBJXQ_FJ.aspx（form1，enctype=multipart/form-data）
   字段：txtPXH(排序号) txtFJMC(附件名称) txtFJMS(附件描述) txtRemark(备注)
        lblPID(记录ID) btnPostFile(提交) FileUploadExcel(文件)
   POST 同一个 action（带上 __VIEWSTATE/__EVENTVALIDATION + 文件）
   → 列表多一行；新增页的 [附件(无)] 变成 [附件(有)]

下载：行内「附件文件：有」→ Display_FJ(文件ID,PID) → 回发 btn_Display_FJ
   → window.open('…/Admin/FuJian/BJ_KHBJXQ/Picture/<guid>.<ext>')
```

- **必须先有记录 ID**：新建页（未保存）点附件不会弹窗，所以 `erp_quote_create` 是
  「先落库、再上传」。
- 文件本体是 **静态直链**，HTTP GET 即可下载（不需要 cookie）。
- ⚠ **不同模块的文件落在不同端口**：客户订单在 `:8887`，报价需求在 `:8889`。
  下载时会在 `cfg.host` 与 `cfg.captcha_host` 之间自动回退。

## 参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `record_id` | str | 报价需求记录 ID（**必填**） |
| `files` | list | 要上传的附件；**不传 = 只列出现有附件** |
| `with_urls` | bool | 列表时一并解析下载直链（每个文件多一次回发） |
| `download` | str | 按附件名称（或文件 ID）下载一个附件，返回 `content_base64` |

### `files[]` 支持的写法

| 内容来源 | 写法 |
| --- | --- |
| base64（AI 最常用） | `{"filename": "a.pdf", "content_base64": "JVBERi0…"}` |
| base64 别名 | `{"filename": "a.pdf", "content": "…"}` / `{"base64": "…"}` |
| data URI | `{"filename": "a.pdf", "data_uri": "data:application/pdf;base64,…"}` |
| 纯文本 | `{"filename": "note.txt", "content_text": "第一行\n第二行"}` |
| 本机文件（CLI） | `{"path": "out/a.pdf"}` |

可选字段：`name`（ERP 里的「附件名称」，默认取 `filename`）、`desc`（附件描述）、
`remark`（备注）、`content_type`（默认按扩展名猜）。

上限：单文件 **20 MB**（可用环境变量 `ERP_ATTACH_MAX_MB` 调整）。

## 返回

```json
{
  "ok": true,
  "record_id": "0c3e32d9-…",
  "uploaded": [
    {"filename": "AI生成摘要.txt", "name": "AI生成摘要.txt", "size": 59, "content_type": "text/plain"}
  ],
  "errors": [],
  "verified": true,
  "missing": [],
  "count": 2,
  "files": [
    {"文件ID": "c90650a2-…", "名称": "AI生成摘要.txt", "排序号": "1",
     "描述": "自动上传", "备注": "by erp_tools", "有文件": "有"}
  ]
}
```

`files` 列表每行：`排序号` / `名称` / `描述` / `有文件` / `备注` / `文件ID`（+ `url`、`note` 视参数而定）。

**上传后会回读核对**：附件名称没出现在列表里就返回 `ok=false` 并在 `missing` 里列出来，
不会假报成功。

## CLI

```powershell
# 列表（--urls 顺带取直链）
erp quote-attachment --id <记录ID> --urls

# 上传本机文件（可重复 / 逗号分隔），可指定 ERP 里的名称
erp quote-attachment --id <记录ID> --file out\报价单.pdf --name "客户报价单" --desc "客户发来的原始报价"

# 直接传文本
erp quote-attachment --id <记录ID> --text "AI 摘要：…" --name "AI摘要.txt"

# 下载
erp quote-attachment --id <记录ID> --download "AI摘要.txt" --out-dir out\fj
```

## 注意

- 上传是**写操作**（会真的往 ERP 里加附件），但没有删除接口——删附件要回 ERP 界面。
- 同名附件可以重复上传（ERP 不会去重），核对时按名称匹配，重名会导致 `verified` 判断偏松。
- 「附件列表页」会渲染两份（隐藏/打印表），解析时按文件 ID 去重、按**右对齐列位置**取值，
  否则行首「选择」复选框缺失的老数据行会错列。
- 页面模板与客户订单的 `DD_KHDD_FJ.aspx` 完全一致，通用实现在
  [`tools/attachments.py`](../../src/erp_tools/tools/attachments.py)（`AttachmentPageService`），
  换一个 `page_path` 即可复用到其它模块。
