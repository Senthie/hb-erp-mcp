# `erp_quote_update_product` — 修改单条产品明细（**会写入 ERP**）

> 菜单：**业务报价管理 › 业务需求 › 客户报价需求 › 列表-客户报价需求**
> 页面：`BaoJia/BJ_KHBJXQ/Add.aspx` → `Add_CPZL.aspx`｜CLI：—｜**写**

## 用途

改某条**产品明细**的字段。最常用的是 **产品描述 `CPMS`**（明细行里那段多行长文本），
另外还有名称 / 规格 / 数量，以及 `fields` 里的任意明细字段。

保存后**逐个回读改动过的控件**做复核；不一致返回 `status=failed`，
并在 `mismatched` 里点名是哪个字段没落库。

## 表头 vs 明细（最容易搞混的地方）

| 想改什么 | 用哪个工具 | 字段 |
| --- | --- | --- |
| 单据备注 / 联系人 / 电话 / 折扣率 / 业务员 / 跟单员 | `erp_quote_update` | 表头控件 |
| 表头的「产品描述」 | `erp_quote_update`（`product_memo`） | `txtCPMX` |
| **明细行的产品描述（CPMS）** | **本工具**（`product_desc`） | `txtCPMS` |
| 明细的配件描述 / 修改记录 / 检测项目… | 本工具（`fields`） | `txtRemark1` / `txtRemark3` / `txtRemark4`… |

→ `erp_quote_update` 只认表头，改不了明细 CPMS，这是本次修复的反馈点。

**怎么读当前值**：`erp_quote_detail`（按记录 ID/单号）返回里的 `CPMS` 字段就是明细产品描述，
可以「先读 → 改 → 再读」闭环；`erp_quote_update_product` 的返回里也有 `verified.txtCPMS`。

## 参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `record_id` | str | 报价需求记录 ID（必填） |
| `cpid` | str | 明细行 ID，来自 `erp_quote_details`（必填） |
| `name` | str | 新产品名称（`txtCPMC`）；不传则不修改 |
| `spec` | str | 规格如 `210*148`（`txtGG`） |
| `qty` | float | 产品数量，必须 > 0（同时写 `txtCPSL` 与 `txtCPSL1`） |
| `product_desc` | str | **明细产品描述 CPMS**（`txtCPMS`），可含换行；传空串表示清空 |
| `fields` | dict | 其他明细字段：`{别名或控件名: 值}`，见下表 |
| `confirm` | bool | `false`（默认）只返回待修改字段；`true` 才写库 |

### `fields` 支持的键（与 `erp_quote_create` 同一套词汇）

| 别名 | 控件 | 中文 |
| --- | --- | --- |
| `product_desc` | `txtCPMS` | 产品描述 |
| `parts_desc` | `txtRemark1` | 配件描述（是否有配件） |
| `modify_log` | `txtRemark3` | 修改记录 |
| `inspection` | `txtRemark4` | 检测项目 |
| `packing` / `pallet` / `delivery` / `binding` | `txtBZFS` / `txtTBLX` / `txtSHFS` / `txtZDFS` | 包装/台板/送货/装订 |
| `delivery_date` / `expected_date` | `txtJHRQ` / `txtWCRQ` | 交货/完工日期 |
| `attribute` / `product_type` / `cases` / `languages` | `txtCPSX` / `txtCPLX` / `txtTZS` / `txtYYS` | 产品属性/类型/款数/语言数 |

完整清单见 `erp_quote_product_form`（195 个控件里可写的 51 个）。
也可以直接写控件名（`{"txtCPMS": "…"}`）。规则：

- **只读/自动计算的控件会被拒绝**（如 `txtOldCPMS`、`txtCPSLID1..10`、`txtCPID`）；
- **数量不能用 fields 传**，会提示改用 `qty`（要同时写 `txtCPSL` + `txtCPSL1`）；
- 未知键会直接报错并给出候选（如 `desc` → `product_desc`）。

## 返回

```json
{
  "status": "updated",
  "ok": true,
  "record_id": "817fd919-…",
  "cpid": "aa0721cf-…",
  "changes": { "txtCPMS": "皮壳：157g哑粉…\n内文：128g双铜…" },
  "verified_name": "Sleuth and Clue 精装风琴折粘贴揭页书",
  "verified_qty": null,
  "verified": { "txtCPMS": "皮壳：157g哑粉… 内文：128g双铜…" },
  "mismatched": [],
  "message": "已保存并复核一致"
}
```

失败时：`status=failed`，`mismatched` 列出没生效的控件，`verified` 给出回读到的实际值。

## 注意

- `cpid` 是**明细行** ID，不是单据 ID；单据 ID 传错会直接报错。
- **ERP 保存成功也可能残留上一次的校验提示**（页面不刷新 alert）。所以判定只看回读：
  回读一致 → `updated`（若页面有残留提示会附 `warning`）；回读不一致 → `failed`。
  真实案例：改 CPMS 时页面仍带着 `【检测项目】不能为空！`，但内容其实已落库。
- 日期类字段（`delivery_date` 等）回读时**只比日期部分**：ERP 会把 `2026-12-30` 存成
  `2026/12/30 0:00:00`，不这样比会假失败。
- 提交体会先做整页快照，所以未修改的 textarea（配件描述/检测项目）不会被清空；
  产品类别（树形控件）会按产品名从档案回填，否则保存被挡在「请选择【产品类别】的数据！」。

## 实测（2026-11-20，账号 15627）

- `BJXQ261120009` / cpid `aa0721cf…`：追加标记写 CPMS → 独立回读含标记（366→382 字）→
  复原 → 回读与原文一致、无残留标记。
- 同单用 `fields` 回写 `txtRemark1`（配件描述）/ `txtRemark4`（检测项目）原值 → `updated`。
