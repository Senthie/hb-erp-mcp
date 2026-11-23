# `erp_attachment_modules` — 有附件功能的模块清单

> CLI：—｜**只读（不访问 ERP）**

## 用途

列出本仓库已接入「附件」能力的模块，以及各自实测的附件页、入口形态和删除方式。
用于确认 [`erp_attachment`](erp_attachment.md) 的 `module` 取值。

## 参数

无。

## 返回

```json
{
  "modules": [
    {"module": "quote_request", "label": "客户报价需求", "entry": "anchor",
     "detail_page": "/Admin/BaoJia/BJ_KHBJXQ/Add.aspx",
     "list_page": "",
     "attachment_page": "/Admin/BaoJia/BJ_KHBJXQ/BJ_KHBJXQ_FJ.aspx"},
    {"module": "sales_order", "label": "客户订单", "entry": "cell",
     "detail_page": "",
     "list_page": "/Admin/DingDan/DD_KHDD/Index.aspx",
     "attachment_page": "/Admin/DingDan/DD_KHDD/DD_KHDD_FJ.aspx"}
  ],
  "count": 5,
  "note": "上传页全部是同一套模板；差别只在入口与删除按钮"
}
```

`entry` 两种取值：

- `anchor`：详情页上有 `<a id="btnFJ">[附件(无)]</a>`，回发 `__EVENTTARGET=btnFJ`
- `cell`：附件入口在**列表页**单号单元格上（`OnOpenFJ(...)`），回发 `Btn_FJ`

## 注意

- 这是**离线**清单（代码里的注册表），不会去 ERP 探测；实际附件页路径若变化，
  `erp_attachment` 会在首次调用时自动重新发现。
- 新增模块只需在 `src/erp_tools/tools/attachments.py` 的 `MODULES` 里加一行，
  该工具会自动列出。
- 各模块的详细差异表见 [erp_attachment.md](erp_attachment.md)。
