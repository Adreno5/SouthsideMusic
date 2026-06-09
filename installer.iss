#define AppName "Southside Music"
#define AppVersion "v33"
#define AppPublisher "Adreno9135"
#define AppURL "https://github.com/Adreno5/SouthsideMusic"
#define AppExeName "Launch.exe"

[Setup]
AppId={{31449D6E-7E47-4C0D-B2B2-74B094DBAB24}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
DefaultDirName={code:GetDefaultDir}
DefaultGroupName={#AppName}
OutputDir=build.result\installer
OutputBaseFilename=SouthsideMusic_win64_setup
SetupIconFile=icons\app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "launchafter"; Description: "&Launch {#AppName} after setup"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce unchecked

[Dirs]
Name: "{app}"; Permissions: users-modify

[Files]
Source: "build.result\raw\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent runasoriginaluser; Tasks: launchafter

[Registry]
Root: HKLM; Subkey: "Software\{#AppName}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\{#AppName}"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"

[Code]
function GetDefaultDir(Param: string): string;
begin
  if DirExists('D:\') then
    Result := 'D:\Program Files\Southside Music'
  else
    Result := 'C:\Program Files\Southside Music';
end;
