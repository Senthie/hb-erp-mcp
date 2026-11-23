# `erp_attachment` — 通用附件（各模块复用同一套协议）

> 菜单：**客户报价需求 / 客户报价·产品核价 / 生产施工单 / 采购订单 / 客户订单**
> 页面：各模块的 `*_FJ.aspx`｜CLI：`erp attachment`（旧名 `erp quote-attachment` 仍可用）｜**读 + 写**

## 结论：附件页是同一套模板，只有「入口」和「删除按钮」有差别

实测（2026-11-23，账号 15627）五个模块：

| 模块 `module` | 附件页 | 入口 | 上传页 | 删除 |
| --- | --- | --- | --- | --- |
| `quote_request` 客户报价需求 | `BaoJia/BJ_KHBJXQ/BJ_KHBJXQ_FJ.aspx` | 详情页 `<a id="btnFJ">[附件(无)]</a>` | **同一模板** | `Modify=1` 行内 `Delete$N` + `Btn_DeleteFJ` |
| `price` 客户报价 / 产品核价 | `BaoJia/BJ_KHBJ/BJ_KHBJ_FJ.aspx` | 同上 | 同模板（多 `lblCGDDID`） | 同上 |
| `production` 生产施工单 | `ShengChan/SC_SCSGD/SC_SCSGD_FJ.aspx` | 同上 | 同模板（多 `Btn_OtherMessage`） | 同上 |
| `purchase` 采购订单 | `CaiGou/CG_CGDD/CG_CGDD_FJ.aspx` | 同上 | 同模板 | 同上 |
| `sales_order` 客户订单 | `DingDan/DD_KHDD/DD_KHDD_FJ.aspx` | **列表页**单号单元格 `OnOpenFJ('文件ID','产品ID','FJNum')` | 同模板 | 同上 |

**上传页完全一致**（multipart）：

```
GET  /Admin/<模块>/<X>_FJ.aspx?PID=<记录ID>&Modify=0&LoginUserID=<uid>
     form1 字段：txtPXH(排序号) txtFJMC(附件名称) txtFJMS(描述) txtRemark(备注)
                 lblPID(记录ID) lblID(已有附件行ID) btnPostFile FileUploadExcel
POST 同一个 action：
     txtPXH=1 & txtFJMC=名称 & btnPostFile= & FileUploadExcel=<文件名, 字节>
```

**入口只有两种**（工具会自动识别，不用手填附件页路径）：

- `anchor`：详情页 `<a id="btnFJ">` → 回发 `__EVENTTARGET=btnFJ`
- `cell`：列表页单号单元格 `OnOpenFJ(...)` → 回发 `Btn_FJ`（客户订单）

**删除有两套**（`strategy="auto"` 自动挑）：
`Modify=1` 页有行内 `Delete$<行号>` 图标就点它；否则勾选该行 checkbox 再回发 `Btn_DeleteFJ`。

**文件本体**在静态目录 `/Admin/FuJian/<模块>/Picture/<guid>.<ext>`，
HTTP GET 即可下载、不需要 cookie；⚠ 不同模块落在**不同端口**（客户订单 :8887、报价需求 :8889），
下载时会在 `cfg.host` 与 `cfg.captcha_host` 之间自动回退。

> **`url` 是验证过的**：ERP 弹窗里的 href 端口不一定对（实测报价需求给 `:8887`，
> 而文件实际在 `:8889`），直接用浏览器打那串地址会得到一张 **404 错误页**
> （既不显示内容也不触发下载）。所以 `with_urls` 会先 HEAD/GET 探一次候选地址，
> 把能用的写回 `url`，原值保留在 `url_erp`。同一模块只探一次（进程内缓存）。

## 跨网段下载：网关代理链接

ERP 直链是内网地址（如 `http://192.168.1.199:8887/Admin/FuJian/...`），
不在同一网段就**连不上**。所以列表时每行会多一个 `proxy_url`，由网关自己取回再转发：

```
GET /v1/files?path=/Admin/FuJian/BJ_KHBJXQ/Picture/<guid>.zip&t=<exp>.<hmac>
```

| 特点 | 说明 |
| --- | --- |
| 鉴权 | **URL 里的签名**（绑定路径 + 过期时间），不含 api-key，可以直接贴浏览器 / IM / 日志 |
| 有效期 | 默认 1800 秒，`ERP_FILE_PROXY_TTL` 可调；过期后重新调工具拿新链接 |
| 白名单 | 只代理 `/Admin/FuJian/` 前缀，杜绝被当成任意 URL 的 SSRF 跳板 |
| 目标主机 | 固定用 `ERP_HOST`（再回退验证码端口），**不接受调用方指定 host** |
| 基地址 | 显式 `ERP_PUBLIC_BASE` → 当前请求的 Host → `http://127.0.0.1:8765`；客户端用哪个地址连进来就回哪个 |
| 签名密钥 | 与 `auth.db` 同目录的 `proxy.secret`（首次启动自动生成），网关与 CLI 共用，重启不会立刻作废已发链接 |

```powershell
# 拿到链接（AI/MCP 里就是 files[].proxy_url）
erp attachment --module quote_request --id <记录ID> --urls

# 外部网段直接下载
curl.exe -OJ "http://10.0.0.5:8765/v1/files?path=/Admin/FuJian/...&t=1750000000.ab12cd34"
```

> 网关本身要能被对端访问到：`--bind 0.0.0.0:8765`（团队模式还需 `--allow-host` / TLS）。
> 只想让代理生效、不改绑定的话，用 `ERP_PUBLIC_BASE` 指到 nginx 之类的对外地址。

## 参数（一次调用按参数决定动作）

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `module` | str | `quote_request` / `price` / `production` / `purchase` / `sales_order`（也认中文别名） |
| `record_id` | str | 单据记录 ID（**必填**，附件页按记录 ID 组织） |
| `files` | list | 传了=**上传**；每项见下表 |
| `with_urls` | bool | 列表时一并解析下载直链 |
| `download` | str | 按附件名称/文件ID **下载**，返回 `content_base64` |
| `delete` / `file_id` | str | 传了=**删除**（`delete` 可只写名称的一部分） |
| `confirm` | bool | 删除时 `false` 只预览（`would_delete`），`true` 才删 |

**动作优先级**：`files` > `delete`/`file_id` > `download` > 列表。

### `files[]` 支持的写法

| 内容来源 | 写法 |
| --- | --- |
| base64（AI 最常用） | `{"filename": "a.pdf", "content_base64": "JVBERi0…"}` |
| base64 别名 | `{"filename": "a.pdf", "content": "…"}` / `{"base64": "…"}` |
| data URI | `{"filename": "a.pdf", "data_uri": "data:application/pdf;base64,…"}` |
| 纯文本 | `{"filename": "note.txt", "content_text": "第一行\n第二行"}` |
| 本机文件（CLI） | `{"path": "out/a.pdf"}` |

可选：`name`（ERP 附件名称，默认 `filename`）、`desc`、`remark`、`content_type`、
`expected_size`（声明的原始字节数）、`sha256`（声明的摘要）。
单文件上限 20 MB（`ERP_ATTACH_MAX_MB` 可调）。

### 大文件：分片上传（`files[]` 每项带 `chunk_index`）

单个 MCP 调用的 base64 太大时，内容在**送到服务之前**就可能被截断。分片是把同一个文件
拆成几次小调用送上来，服务端暂存到本地目录，**收齐后拼好再一次性传 ERP**：

```json
{"files": [{"filename": "样书照片.zip", "upload_key": "BJXQ261123017",
            "chunk_index": 0, "total_chunks": 3,
            "chunk_base64": "UEsDBBQ…",
            "expected_size": 1929000, "sha256": "9f2c…"}]}
```

| 字段 | 说明 |
| --- | --- |
| `upload_key` | 同一个文件的分片用**同一个 key**（默认取 `filename`）。建议带上单号，避免与别人的同名文件撞车 |
| `chunk_index` / `total_chunks` | 从 0 开始，必须齐全 |
| `chunk_base64` | 这一片的字节（也接受 `chunk`） |
| `expected_size` / `sha256` | 可选但**强烈建议**：拼装后先本地比对，不符就清空暂存重传 |

- 未收齐 → `{"ok": false, "status": "chunk_pending", "chunks": [{upload_key, total,
  received: [0,1], missing: [2]}], "message": "分片还没传完…"}`，继续传缺的即可
- 收齐 → 自动拼装、核对 `expected_size`/`sha256`，然后走正常上传并回报 `size_ok`
- 暂存目录：`%TEMP%\erp_tools_attach_stage`（`ERP_ATTACH_STAGE_DIR` 可改）；
  同名 `upload_key` 的残局超过 **30 分钟**或 `total_chunks` 变了会自动清空

### 上传后的大小校验（防止“假成功”）

上传成功后会把文件**下回来**比对：

| 字段 | 含义 |
| --- | --- |
| `uploaded[].size` | 本次发出去的字节数 |
| `uploaded[].stored_size` | 从 ERP 下回来的真实字节数 |
| `size_ok` | 全部一致才为 `true` |
| `size_mismatch[]` | `{name, sent, stored, reason: "size"|"sha256"}` |

ERP 会老老实实存下“半截文件”，**只看附件名称是查不出来的**——所以 `ok=false` 可能来自：

1. 回读不到附件（`missing`）
2. 内容比声明的 `expected_size` 短 → 直接拒传，`errors[].error` 明说是“内容在送到服务前就被截断了”
3. 落库字节数 ≠ 发出的字节数，或 sha256 不符 → `size_mismatch`

实测（2026-11-23）：直连上传 2,500,000 字节、经网关 3.18 MB 请求体上传 2,500,000 字节，
**落库都与原件逐字节一致** —— 所以截断不是 ERP/网关，而是调用方送来的 base64 已经是半截。
对策：单次 base64 ≤ 1 MB，更大的文件用分片或 `{"path": "…"}`。

## 返回

列表：

```json
{
  "module": "sales_order",
  "record_id": "70f1d06a-…",
  "page": "/Admin/DingDan/DD_KHDD/DD_KHDD_FJ.aspx",
  "count": 1,
  "files": [{"文件ID": "01bd8737-…", "名称": "PO", "排序号": "1",
             "描述": "", "备注": "", "有文件": "有",
             "行号": "0", "复选框": "gridView1$ctl02$0",
             "url": "http://192.168.1.199:8889/Admin/FuJian/DD_KHDD/Picture/….pdf",
             "url_erp": "http://192.168.1.199:8887/…（仅端口不对时出现）",
             "proxy_url": "http://127.0.0.1:8765/v1/files?path=/Admin/FuJian/…&t=…"}]
}
```

上传：`{ok, verified, count, uploaded:[…], errors:[…], files:[…],
verified, missing, size_ok, size_mismatch}`（**回读核对附件名称 + 字节数**）。

删除预览 / 真删：

```json
{"ok": false, "status": "needs_confirmation", "strategy": "row",
 "would_delete": {"名称": "旧.pdf", "文件ID": "…", "排序号": "1"}, "count": 3}

{"ok": true, "status": "deleted", "strategy": "row",
 "deleted": {"名称": "旧.pdf", "文件ID": "…", "排序号": "1"},
 "count": 2, "remaining": ["C.txt", "D.txt"]}
```

## CLI

```powershell
# 列表（--urls 顺带直链）
erp attachment --module sales_order --id <记录ID> --urls
erp attachment --module production  --id <记录ID>

# 上传（本机文件 / 文本）
erp attachment --module quote_request --id <记录ID> --file out\报价单.pdf --name "客户报价单"
erp attachment --module price --id <记录ID> --text "AI 摘要…" --name "AI摘要.txt"

# 下载 / 删除
erp attachment --module purchase --id <记录ID> --download "PO.pdf" --out-dir out\fj
erp attachment --module quote_request --id <记录ID> --delete "旧.pdf" --yes
```

`erp quote-attachment ...` 是 `--module quote_request` 的别名。

## 相关工具

- `erp_attachment_modules`：列出注册表（各模块的附件页、入口形态、删除方式）
- `erp_sales_order_attachment`：客户订单附件的**按日期区间批量**导出（直链清单 / 落盘），
  与本节「按单据 ID 操作」是两种用法
- `erp_quote_create(attachments=[...])`：建单成功后自动上传（内部就是本工具）

## 注意

- 附件页都要求**单据已保存**；新建页（未保存）点附件不会弹窗。
- `url` 是 ERP 内网直链；不在同一网段时用 `proxy_url`（网关转发，见上）。
- 新增模块只要在 `tools/attachments.py` 的 `MODULES` 里加一行（或留空 `page_path` 让它自动发现）。
- 上传/删除后都会回读核对，`ok=false` 表示 ERP 没接受，不会假报成功。
- 上传会额外**下载回来比字节数**（`size_ok`），能揪出“存了半截文件”的假成功；大文件请用分片。
- 删除是**物理删除**，ERP 无回收站；同名附件请优先用 `file_id`。
- 附件列表页会渲染两份（隐藏/打印表）：解析时按文件 ID 去重、按**右对齐列位**取值。
