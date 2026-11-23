# `erp_quote_attachment_delete` — 删除报价需求附件（**会写入 ERP**）

> 菜单：**业务报价管理 › 业务需求 › 客户报价需求**
> 页面：`BaoJia/BJ_KHBJXQ/BJ_KHBJXQ_FJ.aspx?Modify=1`｜CLI：`erp quote-attachment --delete 名称 --yes`｜**删**

## 用途

删掉报价需求单上的**一个附件**，按附件名称或文件 ID 定位。
`confirm=false`（默认）只返回「将会删除哪一条」，不写库。

列表用 [`erp_quote_attachment`](erp_quote_attachment.md)（不传 `files`）。

## 协议（实测 2026-11-23，账号 15627）

```
附件页 ?PID=<记录ID>&Modify=0   → 只读，列表无「删除」列
附件页 ?PID=<记录ID>&Modify=1   → 可编辑：多出「删除」列
    行内删除图标： __doPostBack('gridView1','Delete$<行号>')
    即 POST 时：__EVENTTARGET=gridView1 & __EVENTARGUMENT=Delete$N

另外 Modify=1 页上还有个 Btn_DeleteFJ（× 按钮，配合复选框批量删），
本工具走行内删除（Delete$N），一次删一行，语义更明确。
```

- 行号是**当前列表里的 0 基序号**，删掉一行后序号会重排 → 批量删除由服务层逐条
  「重开页面 → 重新定位 → 删」，不会错位。
- 删完立刻 `Modify=0` 回读，确认该 `文件ID` 已消失，`ok=false` 表示没删掉。

## 参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `record_id` | str | ✅ | 报价需求记录 ID |
| `name` | str | — | 附件名称（可只写一部分，精确匹配优先） |
| `file_id` | str | — | 附件文件 ID（列表里的 `文件ID`，比名称可靠） |
| `confirm` | bool | — | `false`（默认）只预览；`true` 才真正删除 |

`name` 与 `file_id` 至少给一个；都没给或都没命中会报错并列出当前附件名。

## 返回

预览：

```json
{
  "ok": false,
  "status": "needs_confirmation",
  "record_id": "870f2025-…",
  "would_delete": {"名称": "待删A.txt", "文件ID": "0dd42189-…", "排序号": "1"},
  "count": 3,
  "message": "确认删除请传 confirm=true"
}
```

真删：

```json
{
  "ok": true,
  "status": "deleted",
  "record_id": "870f2025-…",
  "deleted": {"名称": "待删A.txt", "文件ID": "0dd42189-…", "排序号": "1"},
  "count": 2,
  "remaining": ["C.txt", "D.txt"],
  "message": ""
}
```

`count` 是删除后剩下的附件数，`remaining` 是剩余附件名（方便直接确认）。

## 注意

- 删除是**物理删除**，ERP 侧没有回收站；同名附件删除时请优先用 `file_id`。
- 需要页面渲染出「删除」列（`Modify=1`）：账号无权限时工具会明确报
  「当前页面没有删除入口」，不会静默失败。
- 只支持单据级附件（`BJ_KHBJXQ_FJ.aspx`）；客户订单的附件页是同一套模板，
  通用实现见 [`tools/attachments.py`](../../src/erp_tools/tools/attachments.py)（`delete_file`）。
