# 打包与分发

> 一句话：**PyInstaller 负责把 Python 变成 `erp.exe`，Inno Setup 负责「选择安装目录 + 快捷方式 + 卸载」的安装界面。**
> PyInstaller 自己**不做**安装向导。

```
uv sync --extra dev
.\.venv\Scripts\python.exe -m PyInstaller packaging\erp-tools.spec --noconfirm
    → dist\erp-tools\erp.exe            （onedir，整个目录一起分发）
ISCC.exe packaging\installer.iss
    → dist\erp-tools-0.3.0-setup.exe    （安装包）
```

或一条命令：`.\scripts\build.ps1`

## 组件版本

| 文件 | 作用 |
| --- | --- |
| `packaging/_entry.py` | 打包入口，等价于 `erp` 命令 |
| `packaging/erp-tools.spec` | PyInstaller 配置（onedir、console、hidden imports） |
| `packaging/installer.iss` | Inno Setup 安装器脚本 |
| `scripts/build.ps1` | 一键：同步依赖 → 测试 → 打包 exe → 编安装包 → 自检 |

## 安装器行为

| 项 | 说明 |
| --- | --- |
| 权限 | `PrivilegesRequired=lowest`，普通用户安装，**不弹 UAC** |
| 安装目录 | 默认 `%LocalAppData%\Programs\erp-tools`，向导中**可自选任意目录** |
| 快捷方式 | 开始菜单（配置向导 / 命令帮助 / 数据目录 / 卸载）；可选桌面图标 |
| PATH | 可选，把安装目录加入**当前用户** PATH（写入 `expandsz`，不破坏原有变量） |
| 安装后 | 自动打开 `erp.exe setup` 配置向导（可取消勾选） |
| 卸载 | 只删程序文件；**不删** `%APPDATA%\erp-tools` 里的配置与 cookie |

## 为什么数据不放在安装目录

安装目录可能被装到 `C:\Program Files`（普通用户不可写）。因此打包运行时：

| 数据 | 位置 |
| --- | --- |
| `.env` 配置 | `%APPDATA%\erp-tools\.env` |
| 登录 cookie（`<工号>.json`） | `%APPDATA%\erp-tools\cookies\` |
| QXID 缓存（`qxid-<工号>.json`） | 同上 |
| 导出文件 | 用户 `--out` 指定的位置（默认当前目录） |

判定依据是 `sys.frozen`（见 `src/erp_tools/core/paths.py`）；也可用环境变量 `ERP_DATA_DIR` 整体覆盖，便于做绿色版或测试。

用 `erp config` 随时查看实际路径：

```
运行方式        PyInstaller 打包
程序位置        C:\Users\me\AppData\Local\Programs\erp-tools\erp.exe
数据目录        C:\Users\me\AppData\Roaming\erp-tools
配置文件        C:\Users\me\AppData\Roaming\erp-tools\.env
Cookie 目录     C:\Users\me\AppData\Roaming\erp-tools\cookies
```

## 配置文件怎么跟着程序走

| 位置 | 优先级 | 说明 |
| --- | --- | --- |
| 命令行 `--host/--user/--password` | 最高 | 临时覆盖 |
| 系统环境变量 `ERP_*` | 高 | CI / 临时切换 |
| `%APPDATA%\erp-tools\config.yaml` | 中 | **用户级**，`erp setup` 写这里 |
| `<安装目录>\config.yaml` | 低 | 随包携带（构建时生成） |
| `.\.env` | 最低 | 旧版机制，仍兼容 |

- 构建时会把 `packaging\config.example.yaml` 复制成 `dist\erp-tools\config.yaml`；
  若要预填账号密码再分发，就放一份 `packaging\config.local.yaml`（已 gitignore），
  构建脚本优先用它：

  ```powershell
  Copy-Item packaging\config.example.yaml packaging\config.local.yaml
  # 编辑 config.local.yaml 填好 host / username / password，再：
  .\scripts\build.ps1
  ```
- 安装器还会把该文件一起装进安装目录；但**程序只读不写**它，用户级改动写到 `%APPDATA%`。
- 示例（`packaging\config.example.yaml`）：

  ```yaml
  erp:
    host: http://192.168.1.199:8889
    captcha_host: ""
    username: "21146"
    password: ""
  runtime:
    max_concurrency: 8
  qxid:
    quote_index: ""
  ```

## 首次使用流程（给最终用户）

1. 运行安装包 → 选目录 → 完成；
2. 勾选「立即运行配置向导」或从开始菜单打开；
3. 向导里填 ERP 地址 / 工号 / 密码 → 自动登录并把 cookie 存进 `%APPDATA%\erp-tools\cookies`；
4. 之后直接用 `erp query` / `erp quote`，无需再登录（cookie 失效会自动重登）。

```powershell
erp config                            # 看配置来源与数据目录
erp setup                             # 重新配置（会更新 .env）
erp query --customer IBL --out out\ibl.csv
erp quote --bdbh BJXQ261105013 --out data\one.jsonl
```

## 注意事项

- **体积**：约 311 MB。其中 onnxruntime + ddddocr 模型占大头
  （`common.onnx` 51.6 MB、`common_det.onnx` 19.2 MB、`common_old.onnx` 13 MB）。
  验证码自动识别是**默认能力**，不需要额外安装；若日后要瘦身，可考虑剔除
  `common_det.onnx`（仅滑块验证码用）并在 `spec` 里排除，但需重新验证登录。
- **首次启动**：onedir 首次运行会被杀软扫描，略慢；后续很快。
- **中文向导界面**：Inno Setup 官方不带简体中文，可自行下载 `ChineseSimplified.isl`
  放到 Inno 的 `Languages\` 目录并在 `[Languages]` 中引用。
- **代码签名**：未签名时 SmartScreen 会提示「未知发布者」，需要证书才能消除。
- **升级安装**：`AppId` 不变即可覆盖安装，用户数据不受影响。
- **不要**把 `dist\erp-tools\erp.exe` 单独拷走——它是 onedir 布局，必须连同同目录文件一起。
