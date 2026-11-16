# `erp_quote_update_product` — 修改单条产品明细（**会写入 ERP**）

> 菜单：**业务报价管理 › 业务需求 › 客户报价需求 › 列表-客户报价需求**
> 页面：`BaoJia/BJ_KHBJXQ/Add.aspx` → `Add_CPZL.aspx`｜CLI：—｜**写**

## 用途

改某条产品明细的名称 / 规格 / 数量。保存后**回读复核**名称与产品数量，
不一致返回 `status=failed`。

## 参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `record_id` | str | 报价需求记录 ID（必填） |
| `cpid` | str | 明细行 ID，来自 `erp_quote_details`（必填） |
| `name` | str | 新产品名称（写入 `txtCPMC`）；不传则不修改 |
| `spec` | str | 规格如 `210*148` |
| `qty` | float | 产品数量，必须 > 0 |
| `confirm` | bool | `false`（默认）只返回待修改字段；`true` 才写库 |

## 返回

```json
{ "status": "updated", "cpid": "…", "readback": { "name": "…", "qty": "3000" } }
```

## 注意

- `cpid` 是**明细行** ID，不是单据 ID；单据 ID 传错会直接报错。
- 数量走的是明细页的 `txtCPSL`（隐藏汇总值），只改 `txtCPSL1` 会报「产品数量不能为空」。
- 明细字段只接受首屏可见的 51 个可写字段。
