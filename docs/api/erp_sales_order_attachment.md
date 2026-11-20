# `erp_sales_order_attachment` — 客户订单附件

> 菜单：**客户订单管理 › 客户订单 › 列表-客户订单**
> 页面：`DingDan/DD_KHDD/Index.aspx`（QXID **687**）＋ 附件静态目录｜CLI：`erp sales-order-attachment`｜**只读**

## 用途

下载客户订单列表里**「表单编号」左击调出的附件**（鼠标悬停提示 `左击即可以调出附件`，
只有**带附件**的单号才是蓝色可点的，否则是纯文本单元格）。

## 结论：有接口，而且附件是静态文件直链

实测（2026-11-20，账号 15627）完整链路：

```
列表页  <td onclick="OnOpenFJ('文件ID','产品ID','FJNum')" title="左击即可以调出附件">KHDD26110042</td>
   │  回发 Btn_FJ（先写 lblID / lblPID / lblFJNum）
   ├─ FJNum=1 → window.open("http://<host>:8887/Admin/FuJian/DD_KHDD/Picture/<guid>.pdf")
   └─ FJNum=2 → window.open("http://<host>:8887/Admin/DingDan/DD_KHDD/DD_KHDD_FJ.aspx?PID=…")
                 附件列表页每行「附件文件：有」→ Display_FJ → 回发 btn_Display_FJ
                 → window.open("…/Admin/FuJian/DD_KHDD/Picture/<guid>.pdf")
```

- 文件本身挂在 ERP 的**静态目录** `/Admin/FuJian/<模块>/Picture/<guid>.<扩展名>`，
  由 **8887 端口**（与验证码同一端口）提供。
- 该直链 **HTTP GET 即可下载，不需要任何 cookie / 会话**（实测带不带 cookie 都返回 200
  `application/pdf`，去掉 `?time=` 也照样能下）。
- `?time=20261120183234 462` 只是时间戳（注意里面有空格），去掉不影响下载。

## 参数

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `record_id` | str | — | 记录 ID（列表的 `记录ID`） |
| `number` | str | — | 表单编号，如 `KHDD26110042` |
| `from_date` / `to_date` | str | 本月 1 号 / 今天 | 日期区间 |
| `where` | str | — | 本地过滤，如 `表单编号~KHDD2611` |
| `filters` | dict | — | 服务端下拉，如 `订单状态=编辑中` |
| `limit` | int | 50 | 最多处理多少条单据 |

按 `record_id` / `number` 精确过滤时仍会先跑一次列表查询（附件入口只存在于列表页）。

## 返回

```json
{
  "count": 2,
  "files": [
    {"表单编号": "KHDD26110041", "记录ID": "084d1a0b-…", "名称": "PurchaseOrder_9783845873374_…",
     "url": "http://192.168.1.199:8887/Admin/FuJian/DD_KHDD/Picture/eb945a61-….pdf?time=…"},
    {"表单编号": "KHDD26110041", "记录ID": "084d1a0b-…", "名称": "PurchaseOrder_9783845873282_…",
     "url": "http://192.168.1.199:8887/Admin/FuJian/DD_KHDD/Picture/6653407a-….pdf?time=…"}
  ],
  "note": "url 可直接 HTTP GET 下载（静态文件，无需会话）"
}
```

单附件（`FJNum=1`）时 `名称` 为空——ERP 这一分支不返回附件名，只能拿到 GUID 文件名。

## CLI（可直接落盘）

```powershell
erp sales-order-attachment --no KHDD26110042                 # 只打印直链
erp sales-order-attachment --no KHDD26110041 --out-dir out\fj # 多附件，下载到目录
erp sales-order-attachment --from 2026-11-01 --to 2026-11-20 --out-dir out\fj --out out\fj.json
```

- 文件名优先用 ERP 的「附件名称」+ 原扩展名；重名时自动追加文件 GUID 前缀，**不会覆盖**已有文件
  （实测有多个附件都叫 `PO`）。
- 整月 2026-11-01~20 实测：**34 个附件 / 7.2 MB**，全部 `application/pdf`。

## 注意

- 只读：只回发附件按钮，不点保存/删除。
- 单个单据最多触发 1 次回发；多附件再逐行触发 1 次（每行 ~0.5 秒），整月 30 单约 40 次请求。
- 「产品附件」（`OnOpenCPFJ` → `Btn_CPFJ`）走同一套机制，`类型` 会标成 `产品附件`；
  客户订单列表里目前只见到订单级附件。
- 8887 端口不通时列表/详情仍可用，但下载会失败——把 `<host>` 换成实际文件服务器地址即可。
