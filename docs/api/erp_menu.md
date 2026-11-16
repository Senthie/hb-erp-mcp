# `erp_menu` — 菜单 → 接口 对照

> 菜单：—（基础设施，不挂在菜单上）
> 页面：`GET /Admin/indexAjax.ashx?FunName=GetMenuTree`｜CLI：`erp menu`｜**只读**

## 用途

回答「这个菜单页有没有接口」：返回 ERP 左侧菜单的覆盖情况、每个已接入页面上的
具体操作（增/删/改/查）、对应的 MCP 工具与文档路径。

完整对照表见 [docs/erp-menu.md](../erp-menu.md)（由 `erp menu --write` 生成）。

## 参数

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `include_tree` | bool | `false` | 是否附带完整菜单树（较大，约 34 个页面 + 分组节点） |

## 返回

```json
{
  "menu_doc": "docs/erp-menu.md",
  "tree_available": true,
  "captured_at": "2026-11-16 10:41:28",
  "account": "21146",
  "login_user": "2474",
  "coverage": { "modules": 10, "pages": 34, "unique_paths": 32,
                "connected_pages": 5, "capabilities": 18,
                "unconnected": 29, "unconnected_paths": 27 },
  "pages": {
    "baojia/bj_khbj/index.aspx": [
      { "kind": "查", "action": "列表筛选 + 导出（一行 = 一个报价产品行）",
        "mcp": "erp_price_list", "cli": "price", "doc": "api/erp_price_list.md" }
    ]
  },
  "infra": [{ "action": "…", "mcp": "erp_whoami", "doc": "api/erp_whoami.md" }],
  "duplicate_paths": { "kucun/kc_clcrksq/add.aspx": ["1036", "331"] },
  "unconnected_paths": ["dingdan/dd_khdd/add.aspx"]
}
```

> `unconnected` 是**菜单页数**（34-5=29），`unconnected_paths` 是**唯一路径数**（32-5=27）——
> 因为 2 个页面各挂在两个菜单下。

## CLI 对应

| 命令 | 作用 |
| --- | --- |
| `erp menu --capture` | 重新抓菜单树 → `docs/menu.tree.json`（需要登录） |
| `erp menu --tree` | 打印菜单树，带 QXID 与 ✅/⬜ 标记 |
| `erp menu --coverage` | 只看覆盖率统计（默认行为） |
| `erp menu --write` | 生成 `docs/erp-menu.md` |
| `erp menu --check` | 校验生成物与代码一致（CI 用，不一致退出码 1） |

## 注意

- 菜单树是**按账号权限**渲染的：换账号或权限调整后要重新 `--capture`，
  文档顶部的「抓取账号 / 抓取时间」就是这个意思。
- 本机没有 `docs/menu.tree.json` 时返回 `tree_available=false`，只给能力清单。
- 同一页面可能挂在多个菜单下（见 `duplicate_paths`），QXID 是**菜单项**维度，
  所以能力清单按「页面路径」而不是「菜单路径」索引。
- 追加能力的流程：改 `src/erp_tools/menu_catalog.py` →
  `erp menu --write` → 测试 `tests/test_menu_docs.py` 保证不会漂。
