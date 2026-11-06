# 依赖与打包脚本
#   .\scripts\build.ps1              # 打包 exe（有 Inno Setup 时顺带出安装包）
#   .\scripts\build.ps1 -SkipInstaller

param(
    [switch]$SkipInstaller,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Push-Location $root
try {
    $py = Join-Path $root '.venv\Scripts\python.exe'

    Write-Host '== 0/4 检查环境 ==' -ForegroundColor Cyan
    if (-not (Test-Path $py)) {
        $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
        if (-not $uv) { $uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe' }
        if (-not (Test-Path $uv)) {
            throw '未找到 .venv 也没有 uv；请先执行：uv sync --extra dev'
        }
        & $uv sync --extra dev
        if ($LASTEXITCODE -ne 0) { throw 'uv sync 失败' }
    }

    & $py -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
        if (-not $uv) { $uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe' }
        if (Test-Path $uv) {
            Write-Host '   安装打包依赖：uv sync --extra dev' -ForegroundColor DarkGray
            & $uv sync --extra dev
        } else {
            Write-Host '   安装 PyInstaller：pip install pyinstaller' -ForegroundColor DarkGray
            & $py -m pip install --quiet "pyinstaller>=6.0"
        }
        if ($LASTEXITCODE -ne 0) { throw '安装 PyInstaller 失败' }
    }

    if (-not $SkipTests) {
        Write-Host '== 2/4 运行测试 ==' -ForegroundColor Cyan
        & .\.venv\Scripts\python.exe -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw '测试未通过，终止打包' }
    } else {
        Write-Host '== 2/4 跳过测试 ==' -ForegroundColor DarkGray
    }

    Write-Host '== 3/4 PyInstaller 打包 ==' -ForegroundColor Cyan
    & .\.venv\Scripts\python.exe -m PyInstaller packaging\erp-tools.spec --noconfirm
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 打包失败' }

    $exe = Join-Path $root 'dist\erp-tools\erp.exe'
    if (-not (Test-Path $exe)) { throw "未找到产物：$exe" }

    Write-Host '   自检：erp.exe tools' -ForegroundColor DarkGray
    & $exe tools > $null
    if ($LASTEXITCODE -ne 0) { throw '打包产物自检失败' }

    # 随包携带配置：优先用本地填好的 config.local.yaml（不入库），否则用模板
    $localConfig = Join-Path $root 'packaging\config.local.yaml'
    $exampleConfig = Join-Path $root 'packaging\config.example.yaml'
    $sourceConfig = if (Test-Path $localConfig) { $localConfig } else { $exampleConfig }
    Copy-Item $sourceConfig (Join-Path $root 'dist\erp-tools\config.yaml') -Force
    Write-Host "   配置文件：dist\erp-tools\config.yaml（来自 $(Split-Path $sourceConfig -Leaf)）" -ForegroundColor DarkGray

    if ($SkipInstaller) {
        Write-Host '== 4/4 跳过安装包（-SkipInstaller） ==' -ForegroundColor DarkGray
        return
    }

    Write-Host '== 4/4 生成安装包（Inno Setup） ==' -ForegroundColor Cyan
    $iscc = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
        'C:\Program Files\Inno Setup 6\ISCC.exe'
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1

    if (-not $iscc) {
        Write-Warning '未找到 Inno Setup 6（ISCC.exe），只产出了 dist\erp-tools\ 目录。'
        Write-Warning '安装后重跑本脚本即可：winget install -e --id JRSoftware.InnoSetup'
        return
    }

    & $iscc packaging\installer.iss
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup 编译失败' }
    Get-ChildItem (Join-Path $root 'dist\*setup.exe') |
        ForEach-Object { Write-Host ("  安装包：{0}  ({1:N1} MB)" -f $_.FullName, ($_.Length / 1MB)) }
}
finally {
    Pop-Location
}
