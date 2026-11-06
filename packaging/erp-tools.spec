# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir）。

构建：
    uv sync --extra dev
    uv run pyinstaller packaging/erp-tools.spec --noconfirm

产物：dist/erp-tools/erp.exe（整目录一起分发，由 Inno Setup 打成安装包）

说明：
  * onedir 而不是 onefile：启动快、便于 Inno 增量覆盖、杀软误报更少；
  * 验证码自动识别（ddddocr + onnxruntime）是默认能力，二者的模型/DLL 通过
    `collect_all` 整包打入，否则运行时会报模型文件缺失；
  * 包体约 311 MB，其中 onnx 模型占大头（见 packaging/README.md）。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH 由 PyInstaller 注入)

# ddddocr 自带 onnx 模型文件，onnxruntime 自带 DLL —— 必须整包收集，
# 否则打出来的 exe 会报「模型文件找不到」或 onnxruntime 加载失败。
datas, binaries, hiddenimports = [], [], ["lxml.etree", "lxml._elementpath"]
for package in ("ddddocr", "onnxruntime"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    # 随包携带的配置模板（用户级配置缺失时作为兜底）
    datas=datas + [(str(ROOT / "packaging" / "config.example.yaml"), ".")],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "_pytest", "setuptools"],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="erp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="erp-tools",
)
