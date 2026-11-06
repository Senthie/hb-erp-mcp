; erp-tools 安装器（Inno Setup 6）
;
; 编译：ISCC.exe packaging\installer.iss
; 产物：dist\erp-tools-<版本>-setup.exe
;
; 设计要点：
;   * PrivilegesRequired=lowest → 普通用户即可安装，不弹 UAC（默认装到
;     %LocalAppData%\Programs\erp-tools，也可在向导里自选其它目录）；
;   * DisableDirPage=no        → 保留「选择安装目录」页面；
;   * 程序数据（.env / cookies / 导出）不放安装目录，统一写 %APPDATA%\erp-tools，
;     所以装到 Program Files 这类只读目录也能正常保存登录态；
;   * 卸载默认不删用户数据（cookie 在 %APPDATA%\erp-tools）。

#define AppName "erp-tools"
#define AppVersion "0.3.0"
#define AppExe "erp.exe"
#define SourceDir "..\dist\erp-tools"

[Setup]
AppId={{8E4B0B2C-6F3A-4C61-9A0E-2D5C7B1F4A93}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=erp-tools-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
ChangesEnvironment=yes
; 安装包内的中文界面需要自行放入 ChineseSimplified.isl（Inno 官方不附带）

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce
Name: "addtopath"; Description: "把 erp 命令加入当前用户 PATH"; GroupDescription: "附加任务："

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\erp 配置向导"; Filename: "{app}\{#AppExe}"; Parameters: "setup"; WorkingDir: "{app}"; Comment: "首次使用请先运行"
Name: "{group}\命令帮助"; Filename: "{app}\{#AppExe}"; Parameters: "tools"; WorkingDir: "{app}"
Name: "{group}\数据目录（cookie / 配置）"; Filename: "{sys}\explorer.exe"; Parameters: """{userappdata}\{#AppName}"""
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\erp 配置向导"; Filename: "{app}\{#AppExe}"; Parameters: "setup"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "setup"; Description: "立即运行配置向导"; Flags: postinstall nowait skipifsilent

[Registry]
; 仅写当前用户 PATH；preservestringtype 保证不把 REG_EXPAND_SZ 变成 REG_SZ。
; 卸载时不移除该条目（避免误伤用户 PATH），只清理程序文件。
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
    ValueData: "{olddata};{app}"; Tasks: addtopath; Flags: preservestringtype
