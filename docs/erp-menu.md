# ERP 菜单 → 接口 对照表

> **自动生成，请勿手改**：`erp menu --write`
> 数据源：`docs/menu.tree.json`（菜单树）+ `src/erp_tools/menu_catalog.py`（能力清单）
>
> | 项 | 值 |
> | --- | --- |
> | 抓取账号 | 21146（ERP UserID 2474）|
> | 抓取时间 | 2026-11-16 10:41:28 |
> | 菜单来源 | `GET /Admin/indexAjax.ashx?FunName=GetMenuTree&Value1=2474&Value2=` |
> | 规模 | 10 个模块 · 34 个菜单页（32 个唯一路径） |
> | 已接入 | 5 个页面 · 18 个接口 |

## 覆盖率

| 模块 | 菜单页 | 已接入 | 未接入 |
| --- | ---: | ---: | ---: |
| 业务报价管理 | 8 | 4 | 4 |
| 客户订单管理 | 2 | 0 | 2 |
| 生产管理 | 1 | 0 | 1 |
| 采购管理 | 6 | 0 | 6 |
| 仓库管理 | 2 | 0 | 2 |
| 设备管理 | 2 | 0 | 2 |
| 人力资源管理 | 8 | 1 | 7 |
| 财务管理 | 2 | 0 | 2 |
| 个人信息 | 3 | 0 | 3 |
| 系统管理 | 0 | 0 | 0 |
| **合计** | **34** | **5** | **29** |

标记说明：✅ 已接入（后面跟接口名与文档）、⬜ 未接入、⚠ 同一页面挂在多个菜单下。

## 菜单树与接口

- **业务报价管理**
  - 业务需求
    - 客户报价需求
      - `新增-客户报价需求` · `BaoJia/BJ_KHBJXQ/Add.aspx` · QXID 873
        - ✅ 增 · 新建报价需求（主表 + 产品明细 + 产品数量） → `erp_quote_create` · [api/erp_quote_create.md](api/erp_quote_create.md)
        - ✅ 查 · 新增页字段结构（能填什么、业务员怎么传） → `erp_quote_new_form` · [api/erp_quote_new_form.md](api/erp_quote_new_form.md)
        - ✅ 查 · 产品明细子页字段清单 + 上传用 JSON Schema → `erp_quote_product_form` · [api/erp_quote_product_form.md](api/erp_quote_product_form.md)
        - ✅ 查 · 业务员 / 跟单员 / 单价体系 下拉选项 → `erp_quote_options` · [api/erp_quote_options.md](api/erp_quote_options.md)
        - ✅ 查 · 客户资料检索（拿 customer_id） → `erp_quote_customer_search` · [api/erp_quote_customer_search.md](api/erp_quote_customer_search.md)
        - ✅ 改 · 改表头（备注 / 联系人 / 折扣率 / 业务员 / 跟单员…） → `erp_quote_update` · [api/erp_quote_update.md](api/erp_quote_update.md)
      - `列表-客户报价需求` · `BaoJia/BJ_KHBJXQ/Index.aspx` · QXID 876
        - ✅ 查 · 列表筛选 + 导出 CSV → `erp query` · `erp_quote_list` · [api/erp_quote_list.md](api/erp_quote_list.md)
        - ✅ 查 · 单张单据完整详情（订单需求 JSON） → `erp quote` · `erp_quote_detail` · [api/erp_quote_detail.md](api/erp_quote_detail.md)
        - ✅ 查 · 列出产品明细行（拿 cpid，供改/删单行） → `erp_quote_details` · [api/erp_quote_details.md](api/erp_quote_details.md)
        - ✅ 改 · 改单条产品明细（名称 / 规格 / 数量）（入口是 Add.aspx，流程从列表的记录ID 进） → `erp_quote_update_product` · [api/erp_quote_update_product.md](api/erp_quote_update_product.md)
        - ✅ 删 · 删除整张报价需求单 → `erp_quote_delete` · [api/erp_quote_delete.md](api/erp_quote_delete.md)
  - 客户报价
    - 客户报价
      - `新增-客户报价` · `BaoJia/BJ_KHBJ/Add.aspx` · QXID 857
        - ✅ 读 · 单个报价产品行完整详情 → `erp price-detail` · `erp_price_detail` · [api/erp_price_detail.md](api/erp_price_detail.md)
        - ✅ 读 · 22 个子数据节点（数量档/部件/工序/辅料/费项/折叠面板…） → `erp price-node` · `erp_price_node` · [api/erp_price_node.md](api/erp_price_node.md)
      - `列表-客户报价` · `BaoJia/BJ_KHBJ/Index.aspx` · QXID 860
        - ✅ 查 · 列表筛选 + 导出（一行 = 一个报价产品行） → `erp price` · `erp_price_list` · [api/erp_price_list.md](api/erp_price_list.md)
  - 报价其它设置
    - 报价纸张价格设置
      - `新增-报价纸张价格设置` · `BaoJia/BJ_Base_BJZZDJSZ/Add.aspx` · QXID 9461
        - ⬜ 未接入
      - `列表-报价纸张价格设置` · `BaoJia/BJ_Base_BJZZDJSZ/index.aspx` · QXID 9464
        - ⬜ 未接入
    - 报价辅料价格设置
      - `新增-报价辅料价格设置` · `BaoJia/BJ_Base_BJCLDJSZ/Add.aspx` · QXID 12537
        - ⬜ 未接入
      - `列表-报价辅料价格设置` · `BaoJia/BJ_Base_BJCLDJSZ/Index.aspx` · QXID 12540
        - ⬜ 未接入
- **客户订单管理**
  - 客户订单
    - `新建-客户订单` · `DingDan/DD_KHDD/Add.aspx` · QXID 684
      - ⬜ 未接入
    - `列表-客户订单` · `DingDan/DD_KHDD/Index.aspx` · QXID 687
      - ⬜ 未接入
- **生产管理**
  - 补数管理 · 无页面
  - `待下工单` · `ShengChan/SC_SCTJFX/SC_DXSCSGDFX.aspx` · QXID 14484
    - ⬜ 未接入
- **采购管理**
  - 采购申请
    - `新增-采购申请` · `KuCun/KC_CLCRKSQ/Add.aspx` · QXID 1036 · ⚠ 同路径另有 QXID 331
      - ⬜ 未接入
    - `列表-采购申请` · `KuCun/KC_CLCRKSQ/Index.aspx` · QXID 1039 · ⚠ 同路径另有 QXID 334
      - ⬜ 未接入
  - 材料询价
    - 部门材料询价
      - `新增-部门材料询价` · `CaiGou/CG_BMCLXJ/Add.aspx` · QXID 1162
        - ⬜ 未接入
      - `列表-部门材料询价` · `CaiGou/CG_BMCLXJ/Index.aspx` · QXID 1165
        - ⬜ 未接入
    - 供应商材料询价
      - `新增-供应商材料询价` · `CaiGou/CG_GYSCLXJ/Add.aspx` · QXID 1171
        - ⬜ 未接入
      - `列表-供应商材料询价` · `CaiGou/CG_GYSCLXJ/Index.aspx` · QXID 1174
        - ⬜ 未接入
- **仓库管理**
  - 材料管理
    - 部门领料
      - 部门领料申请
        - `新增-部门领料申请` · `KuCun/KC_CLCRKSQ/Add.aspx` · QXID 331 · ⚠ 同路径另有 QXID 1036
          - ⬜ 未接入
        - `列表-部门领料申请` · `KuCun/KC_CLCRKSQ/Index.aspx` · QXID 334 · ⚠ 同路径另有 QXID 1039
          - ⬜ 未接入
- **设备管理**
  - 设备维修
    - 设备维修通知单
      - `新增-设备维修通知单` · `SheBei/SB_WXTZD/Add.aspx` · QXID 1663
        - ⬜ 未接入
      - `列表-设备维修通知单` · `SheBei/SB_WXTZD/Index.aspx` · QXID 1666
        - ⬜ 未接入
- **人力资源管理**
  - 人事管理
    - 行政管理
      - 工作联络单
        - `新增-工作联络单` · `RenShi/RS_GZLLD_M/Add.aspx` · QXID 8540
          - ⬜ 未接入
        - `列表-工作联络单` · `RenShi/RS_GZLLD_M/Index.aspx` · QXID 8543
          - ⬜ 未接入
  - 考勤管理
    - `考勤档案` · `RenShi/RS_KQDA_M/Add.aspx` · QXID 2553
      - ✅ 查 · 月度考勤明细 → `erp attendance` · `erp_attendance` · [api/erp_attendance.md](api/erp_attendance.md)
    - 签卡请假
      - 员工签卡
        - `新增-员工签卡` · `RenShi/RS_YGQK_M/Add.aspx` · QXID 2799
          - ⬜ 未接入
        - `列表-员工签卡` · `RenShi/RS_YGQK_M/Index.aspx` · QXID 2802
          - ⬜ 未接入
      - 员工请假
        - `列表-员工请假` · `RenShi/RS_YGQJ_M/Index.aspx` · QXID 2509
          - ⬜ 未接入
      - 员工调休
        - `新增-员工调休` · `RenShi/RS_YGTX_M/Add.aspx` · QXID 14247
          - ⬜ 未接入
        - `列表-员工调休` · `RenShi/RS_YGTX_M/Index.aspx` · QXID 14250
          - ⬜ 未接入
- **财务管理**
  - 员工报销
    - 费用报销申请
      - `新增-费用报销申请` · `YingShouYingFu/YSYF_FYBXSQ_M/Add.aspx` · QXID 2273
        - ⬜ 未接入
      - `列表-费用报销申请` · `YingShouYingFu/YSYF_FYBXSQ_M/Index.aspx` · QXID 2276
        - ⬜ 未接入
- **个人信息**
  - `查看详细` · `Accounts/userinfo.aspx` · QXID 1356
    - ⬜ 未接入
  - `修改密码` · `Accounts/userpass.ASPX` · QXID 1357
    - ⬜ 未接入
  - `修改资料` · `Accounts/usermodify.ASPX` · QXID 1358
    - ⬜ 未接入
- **系统管理** · 无页面

## 基础设施（不挂在菜单上）

- ✅ 查 · 当前 api-key 对应的 ERP 身份与会话状态 → `erp_whoami` · [api/erp_whoami.md](api/erp_whoami.md)
- ✅ 查 · 当前账号能访问哪些页面（QXID 能否探测到） → `erp_capabilities` · [api/erp_capabilities.md](api/erp_capabilities.md)
- ✅ 查 · 菜单 → 接口对照（哪些菜单页已接入）（CLI：erp menu） → `erp_menu` · [api/erp_menu.md](api/erp_menu.md)
- ✅ 查 · 登录 / 缓 cookie / 探测 QXID → `erp login` `erp doctor` `erp qxid` · [docs/cli.md](cli.md)
- ✅ 其他 · 起 MCP 网关 / 换 api-key → `erp serve` `erp key` · [docs/mcp.md](mcp.md)

---

追加能力的步骤：在 `src/erp_tools/menu_catalog.py` 的 `PAGE_CAPABILITIES` 里给对应页面加一行（动作 + 接口名 + `docs/api/<接口名>.md`），然后跑 `erp menu --write`。忘了重新生成时 `tests/test_menu_docs.py` 会失败。
