; 資料助秘刷卡考勤系統 — Windows 安裝程式（Inno Setup 6）
; 建置：python tools\build_windows.py（會先打包程式再呼叫 ISCC）
#ifndef AppVer
  #define AppVer "0.1.0"
#endif
#define AppName "資料助秘刷卡考勤系統"
#define AppExe "ZMSAttendance.exe"

[Setup]
AppId={{14E27361-BB91-4ACA-A110-6DC0517C55C8}
AppName={#AppName}
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
AppPublisher=吳玉柱
AppCopyright=系統開發：吳玉柱 先生 與 Claude AI Opus 5.5
VersionInfoVersion={#AppVer}
DefaultDirName={autopf}\ZMSAttendance
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline dialog
OutputDir=..\dist-installer
OutputBaseFilename=ZMSAttendance-Setup-{#AppVer}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
CloseApplications=force
RestartApplications=no

[Languages]
Name: "cht"; MessagesFile: "ChineseTraditional.isl"

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "捷徑："

[Files]
Source: "..\dist\ZMSAttendance\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"
Name: "{group}\存查與資料資料夾"; Filename: "{localappdata}\ZMSAttendance"
Name: "{group}\解除安裝 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"; Tasks: desktopicon

[Dirs]
Name: "{localappdata}\ZMSAttendance"

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即開啟 {#AppName}"; Flags: nowait postinstall skipifsilent
; 程式內「版本更新」以靜默模式安裝後，自動重新開啟
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: WizardSilent

[Messages]
cht.FinishedLabel=安裝完成。%n%n本系統使用「資料助秘之一般公司人事薪資差勤」的資料庫，請先安裝並設定好人事系統。%n%n匯入檔案的存查資料夾：%n%LOCALAPPDATA%\ZMSAttendance\存查%n（更新或解除安裝都不會刪除）

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    if not UninstallSilent then
      MsgBox('程式已移除。打卡資料仍在人事薪資差勤系統的資料庫裡；存查檔案保留在：' + #13#10 + ExpandConstant('{localappdata}\ZMSAttendance') + #13#10 + '如確定不再需要，可自行刪除該資料夾。', mbInformation, MB_OK);
end;
