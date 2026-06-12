#define MyAppName "Kick Drop Miner"
#define MyAppVersion "2.0.3"
#define MyAppPublisher "MiranoVerhoef"
#define MyAppExeName "KickDropsMiner.WinUI.exe"
#define SourceDir "..\build\release\KickDropsMiner_v2.0.3"

[Setup]
AppId={{7D3A4BB1-DAB1-4E3E-BF78-70D87DBF2F5D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Kick Drop Miner
DefaultGroupName=Kick Drop Miner
DisableProgramGroupPage=yes
OutputDir=..\build\release
OutputBaseFilename=KickDropsMiner_v{#MyAppVersion}_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\Pages"

[Icons]
Name: "{group}\Kick Drop Miner"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\Assets\AppIcon.ico"
Name: "{autodesktop}\Kick Drop Miner"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\Assets\AppIcon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,Kick Drop Miner}"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  if (not Exec(ExpandConstant('{cmd}'), '/C where python', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)) or (ResultCode <> 0) then
  begin
    MsgBox('Python is required for the automation bridge. Install Python and dependencies from requirements.txt before using queue/login automation.', mbInformation, MB_OK);
  end;

  Result := True;
end;
