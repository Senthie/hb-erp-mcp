# `erp_purchase_node` — 采购订单子页面

> CLI：`erp purchase-node --id <ID> --node <节点>`｜**只读**

## 用途

采购订单详情页里能弹出来的**子页面**。实测结论：

> **采购订单没有弹窗数据子页** —— 材料明细已经在 `erp_purchase_detail` 的
> `grids[0]`（`table#gridView2`，33 列）里，不需要再点开任何弹窗。

这里能打开的都是「选择 / 编辑」类弹窗：

| node | 子页 | 打开按钮 | 说明 |
| --- | --- | --- | --- |
| `supplier` | `Base_GYSZL_zixuan.aspx` | `btn_SelectGYS` | 供应商档案选择页（选择弹窗） |

其它弹窗（收货地址 `Modify_SelectKHSHDZ.aspx`、客户 `Base_KHZL_Select.aspx`、
备注编辑器 `Base_Remark_Select.aspx`）是**前端 `showModalDialog` 直接打开**的，
不经过「写隐藏字段 + 回发按钮」，因此不作为节点暴露。

## 参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `record_id` | str | 采购订单记录 ID |
| `node` | str | 节点名；`list` = 列出全部并标注这一单能否打开 |

## 返回

与 `erp_production_node` 同形状：`{node, node_label, page, url, fields, grids, texts,
dropped_controls, title}`。

## 注意

- 如果你的场景确实需要「收货地址选择页」的选项列表，请改用
  `erp_purchase_detail` 的 `fields["送货地址"]`（已带出当前值），
  或直接调 `Modify_SelectKHSHDZ.aspx?PID=<txtWLDWID>`（PID 是订单 ID）。
