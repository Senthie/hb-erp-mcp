# Python API（嵌入到别的程序）

`erp_tools.api` 只做「拿数据并返回 Python 对象」——**不落盘、不 print、不解析参数**。
适合嵌进别的系统、写自己的调度逻辑，或做二次封装。

- 要跑批/导表 → 用 [CLI](cli.md)
- 要给 AI 客户端用 → 用 [MCP 服务](mcp.md)

## 1. 安装与导入

```powershell
uv sync              # 最小依赖；不需要 server extra
```

```python
from erp_tools.api import (
    connect, qxid_map,
    query_quotes, fetch_quote, fetch_quote_by_bdbh, iter_quotes,
    fetch_attendance,
)
```

## 2. 建立会话：`connect()`

```python
def connect(
    *,
    username: str | None = None,
    password: str | None = None,
    host: str | None = None,
    env_file: str | None = None,
    login: bool = True,
    interactive: bool = False,
    captcha: str | None = None,
) -> AuthManager
```

| 参数 | 说明 |
| --- | --- |
| `username` / `password` | 覆盖配置里的账号密码；`password` 只在登录那一次用到 |
| `host` | 覆盖 ERP 地址，如 `http://192.168.1.199:8889`（同时重置验证码端口推导） |
| `env_file` | 指定 `.env` 路径；`None` 时自动查找 `./.env` 与 `%APPDATA%\erp-tools\.env` |
| `login` | 默认 `True` 会**验证登录态**；`False` 只构造客户端（后面自己 `auth.login()`） |
| `interactive` | 验证码 OCR 失败时是否允许人工输入；服务/脚本里必须是 `False` |
| `captcha` | 直接给定验证码文本（调试/特定环境用） |

```python
auth = connect(username="21146", password="***")
print(auth.user_id)          # ERP LoginUserID
```

登录态会缓存到 `COOKIE_DIR/<工号>.json`，下次 `connect()` 直接复用，不必重复登录。
`AuthManager` 是**线程内安全**的（内部按线程持有 `requests.Session`），
可跨工具复用同一个实例。

## 3. 函数一览

| 函数 | 返回 | 说明 |
| --- | --- | --- |
| `qxid_map(auth, *, refresh=False)` | `dict[str, str]` | 「页面路径 → QXID」映射 |
| `query_quotes(auth, *, from_date=None, to_date=None, where=None, key="", field="", limit=None)` | `list[dict]` | 报价需求**列表**，一行一个 dict（38 列） |
| `fetch_quote(auth, record_id, *, fallback_ywy=None)` | `dict` | 单条报价需求**详情**（ERP 订单需求 JSON） |
| `fetch_quote_by_bdbh(auth, bdbh)` | `dict` | 按**表单编号**取详情（内部先查列表拿记录 ID） |
| `iter_quotes(auth, *, csv=None, ids=None, limit=None, workers=None)` | `Iterator[dict]` | 批量采集，惰性产出（不落盘） |
| `fetch_attendance(auth, month, *, start_day="01", end_day="31", username=None, page_size=None)` | `list[dict]` | 月度考勤明细 |

### `query_quotes`

```python
rows = query_quotes(auth, from_date="2026-11-01", to_date="2026-11-11",
                    where="报价状态~需求审批")
```

- `where` 语法与 CLI 一致：逗号分隔，支持 `=` `!=` `~`（包含）`>=` `<=` `>` `<`；
  `>=`/`<=` 对数字按数值比较、对日期按字面比较。
- **日期区间是服务端必填**：留空时 ERP 会套默认区间并**忽略关键字**，这条路径很容易查错。
- 一行示例如下（列名即中文，未做重命名）：

```python
{
  "表单编号": "BJXQ261105013",
  "客户名称": "IBL10",
  "业务员": "许月霞",
  "产品名称": "2027 Tender 贴纸骑马钉书 701(FSC)",
  "数量描述": "3000",
  "报价状态": "需求审批",
  "记录ID": "8c3a02cf-8df0-45b1-9a38-1c84b3548464",
  # ... 共 38 列
}
```

### `fetch_quote` / `fetch_quote_by_bdbh`

```python
data = fetch_quote(auth, "33e3099b-f9bc-4c36-925d-4b10fff7284a")
one = fetch_quote_by_bdbh(auth, "BJXQ261105013")
```

返回结构是「ERP 客户订单需求」字段字典（`BDBH` / `KHMC` / `CPMC` / `CPSL1..10` / `SL` …
以及 `Remark1/3/4` 等），字段含义见
[quote_request/README.md](../src/erp_tools/tools/quote_request/README.md)。

> `CPSL1..10` 是**多语言数量明细**，页面没有明细时是 `None`（**不会用汇总数量兜底**）。
> 汇总数量在另一个字段里——两者语义不同。

### `iter_quotes`

```python
for record in iter_quotes(auth, csv="orders.csv", limit=50, workers=6):
    handle(record)
```

`csv` 可以是没有 `记录ID` 列的原始单号表；内部会按单号日期反查记录 ID，
再并发拉详情（默认并发取配置，ERP 4~8 即饱和）。

### `fetch_attendance`

```python
rows = fetch_attendance(auth, "2026-11", end_day="30", username="21146")
```

- `start_day`/`end_day` 只能是 `01`–`30`（ERP 页面没有 31，传 31 只会回 1 号一行）。
- `username` 是**工卡号**；不给则查当前账号。
- 查不到通常是账号数据权限只到本部门。

## 4. 异常

| 异常 | 含义 | 建议 |
| --- | --- | --- |
| `ConfigError` | 配置缺失/非法（如没填 host） | 检查 `config.yaml` / 环境变量 |
| `AuthError` | 登录被拒（账号密码错、验证码错、登录未生效） | 不要盲目重试，会触发账号锁定 |
| `NetworkError` | 连不上 ERP / 超时 | 可重试；**不是**账号问题 |
| `UpstreamError` | ERP 返回 5xx 或结构异常 | 记录响应再重试 |
| `ParseError` | 页面结构与预期不符（多半是 ERP 改版） | 需要更新解析器并补夹具 |
| `ErpError` | 以上异常的公共基类 | 兜底捕获 |

```python
from erp_tools.core.errors import ErpError, NetworkError

try:
    rows = query_quotes(auth, from_date="2026-11-01")
except NetworkError:
    ...   # 可重试
except ErpError as exc:
    ...
```

## 5. 直接用底层服务（进阶）

业务写入（新增/修改/删除报价需求）没有包进 `erp_tools.api`，直接用服务类：

```python
from erp_tools.tools.quote_create import QuoteCreateService

svc = QuoteCreateService(auth)
svc.search_customers("HAS", limit=10)
svc.prepare(customer="HAS10", products=[{"name": "512961503", "spec": "75*28*298", "qty": 3000}])

created = svc.create(customer="HAS10", sales="陈纯", confirm=True, products=[{
    "name": "512961503", "spec": "75*28*298", "qty": 3000, "unit": "个",
    "category": "STD彩盒", "binding": "胶装/PAD书", "packing": "纸箱包装",
    "pallet": "07", "delivery": "FOB(NGV)",
    "delivery_date": "2026/11/20", "expected_date": "2026/11/20",
    "parts_desc": "无", "modify_log": "无", "inspection": "无",
    "product_desc": "API 测试", "attribute": "三岁以上",
    "product_type": "新产品", "cases": 1, "languages": 1,
}])
cpid = svc.list_details(created["record_id"])["details"][0]["cpid"]
svc.update_product(created["record_id"], cpid, qty=4200, confirm=True)
svc.delete_quote(created["record_id"], confirm=True)
```

所有写入方法都是 `confirm=False` 默认只预演，**必须显式传 `confirm=True` 才会落库**，
且都会写后回读校验。能力与已知限制见
[quote_create/README.md](../src/erp_tools/tools/quote_create/README.md)。

## 6. 与 MCP 服务复用同一套代码

服务端的会话池、api-key 存储也可以直接用：

```python
from erp_tools.apps.server.state import build_state
from erp_tools.apps.server.config import ServerConfig
from erp_tools.runtime.sessions import SessionProvider

provider = SessionProvider(erp_cfg, global_concurrency=6)
session = provider.login("21146", "***")     # 密码只在这一层存在
rows = query_quotes(session.auth, from_date="2026-11-01")
```

`SessionProvider.login()` **不保存密码**：登录后立刻用「无密码 + cookie」重建客户端。
服务端用完即弃，所以 ERP 会话过期后只能重新登录。

## 7. 并发与限流

- 实测该 ERP **4~8 并发即饱和**（8/16 线程时单请求从 ~0.95s 涨到 ~1.6s，吞吐不再上升）。
- 并发上限属于「ERP 实例」而不是「每个工具」，所以配置项是全局的
  （`ERP_MAX_CONCURRENCY` / `--concurrency`），别为每个工具各开一个线程池。
- `ERP_REQUEST_DELAY` 可给全局最小请求间隔（秒），默认 0。
- 老旧 IIS 7.5 上 Keep-Alive 易导致连接池超时，客户端统一发 `Connection: close`。

## 8. 已知限制

- 报价需求页解析依赖中文标签，ERP 改文案需同步更新 `tools/quote_request/parser.py` 的
  `PRODUCT_FIELD_LABELS` / `TEXTAREA_LABELS` 并补夹具。
- cookie 以**明文**保存在 `cookies/<工号>.json`；建议收紧目录 ACL（接 DPAPI 尚未实现）。
- 一次 `create()` 只支持 1 条产品明细，多条请分次调用。
