# `quote` — 客户报价需求【详情】采集

> 页面：`/Admin/BaoJia/BJ_KHBJXQ/Add.aspx?id=<GUID>&Option=3&QXID=873&MenuTree=1`
> 用途：把报价需求单页面解析成「ERP 客户订单需求」JSON（字段名与 ERP 一致，可直接用于录入）。

## 命令

```powershell
.\.venv\Scripts\erp.exe quote [选项]
```

三种指定记录的方式，可混用：

| 方式 | 说明 |
| --- | --- |
| `--id <GUID>` | 已知记录 ID（列表页的 `记录ID` 列）；可重复 |
| `--bdbh <表单编号>` | 只知道单号，如 `BJXQ261105013`；自动用单号里的日期反查记录 ID；可重复或逗号分隔 |
| `--csv <文件>` | 批量：列名需含 `id` 或 `记录ID`；若有 `制单人` 则作为业务员兜底 |

## 选项

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--id GUID` | — | 记录 ID，可重复 |
| `--bdbh 单号` | — | 表单编号，可重复/逗号分隔 |
| `--csv 路径` | — | 批量输入 CSV |
| `--out 路径` | 不写文件 | 输出 JSONL（追加写，支持断点续跑） |
| `--limit N` | 不限 | 只处理前 N 条 |
| `--workers N` | 8（`ERP_MAX_CONCURRENCY`） | 并发线程数 |
| `--no-resume` | 关闭 | 忽略已完成的记录全部重跑 |
| `--progress-every N` | 100 | 每 N 条打印一次进度 |

## 示例

```powershell
# 单个单号取详情
erp quote --bdbh BJXQ261105013 --out data\one.jsonl

# 多个单号
erp quote --bdbh BJXQ261105013,BJXQ261105012 --out data\two.jsonl

# 列表导出的 CSV 直接喂进来（含 记录ID 列）
erp quote --csv out\q.csv --out data\detail.jsonl

# 全量（18496 条，约 2 小时，可 Ctrl+C 后重跑续传）
erp quote --csv orders.csv --out data\quote.jsonl

# 抽样验证
erp quote --csv orders.csv --limit 50 --out data\sample.jsonl
```

## 输出格式

JSONL：**一行一条记录**，字段为 ERP 客户订单需求字段。

```json
{
  "id": "8c3a02cf-8df0-45b1-9a38-1c84b3548464",
  "BDBH": "BJXQ261105013",
  "YWY": "许月霞",
  "KHZL": "IBL10",
  "CPMC": "2027 Tender 贴纸骑马钉书 701(FSC)",
  "GG": "216*280*0",
  "CD": 216, "KD": 280, "GD": 0,
  "TZS": 1, "YYS": 1,
  "CPSL1": 3000,
  "CPSL2": null,
  "Remark1": "否",
  "Remark3": "新款报价",
  "Remark4": "",
  "CPMS": "……产品描述（含历史补充描述）……"
}
```

（实际是单行 JSON，这里为阅读方便换行。）

### 字段说明

| 字段 | 含义 / 来源 |
| --- | --- |
| `id` | 记录 ID（GUID），续跑去重的主键 |
| `BDBH` | 表单编号 `txtBDBH` |
| `YWY` | 业务员（下拉 `txtYGXM`；为空时回退 CSV「制单人」） |
| `KHZL` | 客户资料/客户代码（如 `IBL10`） |
| `GDY` | 跟单员 |
| `CPMC` / `CPLB` / `GG` | 产品名称 / 产品类别 / 规格 |
| `CD` `KD` `GD` | 由 `规格` 的 `长*宽*高` 解析出的整数 |
| `TZS` / `YYS` | 款数 / 语言数，从产品描述里的「共【N】款」「每款【N】语言」提取 |
| `CPSL1`…`CPSL10` | 多语言产品数量明细；页面没有明细时为 `null`（**不使用汇总数量兜底**） |
| `ZDFS` / `BZFS` / `TBLX` / `SHFS` | 装订方式 / 包装方式 / 台板类型 / 送货方式 |
| `KHDDH` | 香港单号 |
| `KHCPMC` | 客户单号 |
| `SFYPJ` | 是否有配件 |
| `Remark` | 备注（明细区「备注」行，空则回退 `txtRemark`） |
| `Remark1` / `Remark3` / `Remark4` | 配件描述 / 修改记录 / 检测项目 |
| `CPMS` | 产品描述全文（含历史补充描述） |
| `CPMX` | 产品明细 `txtCPMX` |
| `PXH` | 固定 `1` |
| `chkZDBJF` / `chkBSYTB` / `chkBSYJZX` | 固定 `false` |
| 其余（`CPDL` `DWMC` `JHRQ` `FSCMC` `WCRQ` `DKMC` `YSLX` `SHDQ` `KHDQ` `BGMS` …） | 页面上没有可靠来源，保持 `null` |

> 字段名与 ERP 的录入接口一致，目的是「抓下来就能录」。

## 断点续跑

`--out` 指向的 JSONL 若已存在，会先读取其中所有 `id`，跳过已完成的记录：

```
[INFO] 断点续跑：输出里已有 18200 条，将跳过
[INFO] 共 18496 条，待处理 296 条，并发 8
```

进程被 Ctrl+C 中断时，已写入的行不受影响。

## Python API

```python
from erp_tools.api import connect, fetch_quote, fetch_quote_by_bdbh, iter_quotes

auth = connect(username="21146", password="***")

data = fetch_quote_by_bdbh(auth, "BJXQ261105013")   # 只给单号
data = fetch_quote(auth, "8c3a02cf-…-1c84b3548464") # 给记录 ID

for rec in iter_quotes(auth, csv="orders.csv", limit=10):
    print(rec["BDBH"], rec["CPMC"])
```

## 协议要点 / 坑

- 页面有两种布局：产品描述/备注等「标签 → textarea」行可能在 `td#tdCPZLLIst`，
  也可能在 `div#Pan_gridView7`——必须多作用域按优先级回退。
- 这些 textarea **没有 name/id**，只能靠中文标签定位；工具栏的 `【产品描述】` 链接要排除。
- `CPSL` 明细与「数量：」汇总是两回事：汇总的 onclick 第一个参数固定是 `'-1'`，必须排除。
- 报价页目前**不带 Cookie 也能读**，但工具统一先登录，避免 ERP 收紧后失效。
- 业务员为空时用 CSV「制单人」兜底（已确认可接受）。

## 相关

- 先用 [`../quote_index/README.md`](../quote_index/README.md) 查出记录 ID / 表单编号。

## 退出码

`0` 成功 · `1` 配置/网络/解析错误 · `130` Ctrl+C 中断
