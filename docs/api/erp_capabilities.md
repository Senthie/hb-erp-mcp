# `erp_capabilities` — 当前账号能访问哪些页面

> 菜单：—（基础设施，不挂在菜单上）
> 页面：`GET /Admin/indexAjax.ashx?FunName=GetMenuTree`｜CLI：`erp qxid`｜**只读**

## 用途

检查当前账号能访问哪些页面。返回两段：

* `pages` / `missing_pages`：**基础模块**（报价需求列表/详情、客户报价列表/详情、考勤）的菜单 QXID；
* `modules` / `module_missing`：**产品核价 / 生产施工单 / 采购订单** 三个模块的
  **实测** QXID 与可用性（菜单里能否找到该列表页）。

`missing_pages` 非空 = 当前账号没有该页面权限，调对应工具会失败。

⚠ **先看 `pages_known`**：

| 字段 | 含义 |
| --- | --- |
| `pages_known` | `false` = 菜单**没取到**（网络/接口失败），下面的全 null **不代表没权限** |
| `pages_source` | 本次 `pages` 从哪来：`login`（登录时探的）/ `cache`（磁盘 `cookies/qxid-<工号>.json`）/ `probe`（临时补探）/ `none` |
| `pages_error` | 取不到时的原因（仅在这种情况下出现） |
| `modules.*.available` | 真正权限：`true` / `false` / **`null` = 不知道** |

## 参数

无。

## 返回

```json
{
  "username": "15627",
  "pages": { "quote_index": "876", "quote": "873",
             "price_index": "860", "price": "857", "attendance": "2553" },
  "missing_pages": [],
  "modules": {
    "costing":    {"label": "产品核价",   "list_qxid": "1281", "list_available": true,
                   "detail_path": "BaoJia/BJ_KHBJ/Add.aspx", "detail_reached_from": "list_row"},
    "production": {"label": "生产施工单", "list_qxid": "740",  "list_available": true,
                   "detail_path": "ShengChan/SC_SCSGD/Add.aspx", "detail_reached_from": "list_row"},
    "purchase":   {"label": "采购订单",   "list_qxid": "266",  "list_available": true,
                   "detail_path": "CaiGou/CG_CGDD/Add.aspx", "detail_reached_from": "list_row"}
  },
  "module_missing": [],
  "pages_known": true,
  "pages_source": "cache",
  "note": "modules 里的 QXID 是实测值：同一 aspx 常挂在多个菜单下…"
}
```

菜单没取到时（例如 zhangshuang 这种在 ERP 里没配菜单的账号，或刚重启还没探到）：

```json
{
  "username": "zhangshuang",
  "pages": { "quote_index": null, "quote": null, "price_index": null,
             "price": null, "attendance": null },
  "missing_pages": ["quote_index", "quote", "price_index", "price", "attendance"],
  "pages_known": false,
  "pages_source": "none",
  "pages_error": "菜单接口没有返回任何菜单项，可能权限不足或接口变更",
  "modules": { "costing": { "list_available": false, "available": null, "note": "菜单没取到…" } },
  "note": ["…", "菜单没取到，pages 全 null 不代表没有权限：业务工具走 qxid_for()，探测失败时会回退到内置默认 QXID，通常仍可正常调用。"]
}
```

## 坑：网关重启后「所有人都没权限了」（2026-11-26 已修）

`pages` 只在 `POST /v1/auth/login` 那一刻填。网关重启后会话是从磁盘 cookie
恢复的（`SessionProvider._restore`），旧版本**不回填** `pages` →
`erp_capabilities` 对**任何账号**（包括 15627 这种 227 个菜单页的全权限账号）
都返回 `pages` 全 null、四个模块 `list_available=false`。

现在恢复会话时会先用 `cookies/qxid-<工号>.json` 回填（纯本地、不打 ERP），
`erp_capabilities` 再兜一层「内存 → 缓存 → 真探一次」，所以重启之后依然是
`pages_source=cache` 且五个页面齐全（15627 实测）。

如果看到 `pages_known=false` 且 `pages_error` 提示「菜单接口没有返回任何菜单项」：
那就是**这个账号在 ERP 里确实没配菜单**（例如 zhangshuang），不是网关的问题；
业务工具仍可调用（`qxid_for()` 会回退到内置默认 QXID，会话有效即可）。

## 注意

- **`pages` 是菜单探测值，`modules` 是实测值**，两者口径不同，别混用：
  同一 aspx 会挂在多个菜单下（`BJ_KHBJ/Index.aspx` = 客户报价 860 + 产品核价 1281；
  `SC_SCSGD/Index.aspx` = 740 + 983），`parse_menu_urls` 用 `setdefault` 只留第一个。
  拿 860/983 去查会打开**别的菜单**的页面（症状：核价列表只有 56 列、施工单单号变 `ZBSQ…`）。
- 采购订单**没有新增/详情菜单**（详情从列表行双击进入 `Add.aspx`），
  所以 `detail_reached_from=list_row`，可用性跟列表页一致。
- 菜单接口**必须用 GET**，POST 返回空体。
- 完整菜单树见 [erp-menu.md](../erp-menu.md)。
