; ============================================================================
;  Inno Setup script for the agentteam desktop bundle (per-user install).
;
;  The payload directory is produced by scripts\build-installer.ps1 and looks
;  like this:
;
;      payload\
;        python\               embeddable CPython + Lib\site-packages
;        agentteam-cli.cmd     CLI entry point
;        agentteam-web.cmd     Web UI entry point
;        .env.example          configuration template
;        README.txt            short read-me
;
;  Compile with:
;      iscc /DAppVersion=0.1.0 /DPayloadDir=<abs> /DOutDir=<abs> packaging\agentteam.iss
;  or simply:
;      scripts\build-installer.ps1
;
;  Requires Inno Setup 6.0+ (winget install JRSoftware.InnoSetup).
; ============================================================================

#define AppName "agentteam"
#define AppPublisher "agentteam"
#define AppExeName "agentteam-web.cmd"

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef PayloadDir
  #define PayloadDir "..\build\installer\payload"
#endif
#ifndef OutDir
  #define OutDir "..\dist"
#endif
#ifndef WithChinese
  ; set to 1 by scripts\build-installer.ps1 when a translation is available
  #define WithChinese 0
#endif
#ifndef ChineseMessagesFile
  ; absolute path of ChineseSimplified.isl (the compiler ships no Chinese)
  #define ChineseMessagesFile ""
#endif

[Setup]
AppId={{7C4F1A62-9E3B-4D27-B5A8-3C6D9E0F2B41}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no
PrivilegesRequired=lowest
OutputDir={#OutDir}
OutputBaseFilename=agentteam-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
#if WithChinese
Name: "chinese"; MessagesFile: "{#ChineseMessagesFile}"
#endif
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式 / Create a desktop shortcut"; GroupDescription: "附加任务 / Additional icons"

[Dirs]
Name: "{localappdata}\agentteam"; Flags: uninsneveruninstall
Name: "{localappdata}\agentteam\workspace"; Flags: uninsneveruninstall
Name: "{localappdata}\agentteam\runs"; Flags: uninsneveruninstall

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\.env.example"; DestDir: "{localappdata}\agentteam"; DestName: ".env"; Flags: onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{group}\agentteam Web UI"; Filename: "{app}\agentteam-web.cmd"; WorkingDir: "{app}"; Comment: "启动浏览器里的 agentteam 控制台"
Name: "{group}\agentteam 命令行（示例目标）"; Filename: "{app}\agentteam-cli.cmd"; WorkingDir: "{app}"; Comment: "命令行跑一次内置示例目标"
Name: "{group}\会话记录（runs）"; Filename: "{localappdata}\agentteam\runs"
Name: "{group}\配置文件（.env）"; Filename: "{localappdata}\agentteam\.env"
Name: "{group}\说明文档"; Filename: "{app}\README.txt"
Name: "{group}\卸载 agentteam"; Filename: "{uninstallexe}"
Name: "{autodesktop}\agentteam Web UI"; Filename: "{app}\agentteam-web.cmd"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\agentteam-web.cmd"; Description: "立即启动 agentteam Web UI"; Flags: postinstall nowait skipifsilent
Filename: "{app}\README.txt"; Description: "查看说明文档"; Flags: postinstall shellexec skipifsilent unchecked

[UninstallDelete]
Type: filesandordirs; Name: "{app}\python\__pycache__"
