#define AppName "Southside Music"
#define AppVersion "v44"
#define AppPublisher "Adreno9135"
#define AppURL "https://github.com/Adreno5/SouthsideMusic"
#define AppExeName "Launch.exe"
; Launch.exe lives in launcher\ together with its Nuitka runtime: the exe
; cannot resolve python314.dll from another folder, and a GIL-build
; python314.dll in {app} crashes the app's free-threaded Python (scipy
; imports ctypes and Windows then loads the wrong DLL).
#define AppExePath "launcher\Launch.exe"

[Setup]
AppId={{31449D6E-7E47-4C0D-B2B2-74B094DBAB24}
AppMutex=Local\SouthsideMusicLauncher
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
DefaultDirName={code:GetDefaultDir}
DefaultGroupName={#AppName}
OutputDir=build.result\installer
OutputBaseFilename=SouthsideMusic_{#AppVersion}_win64_setup
SetupIconFile=icons\app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile=LICENSE
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExePath}
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "launchafter"; Description: "&Launch {#AppName} after setup"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce unchecked

[Dirs]
Name: "{app}"; Permissions: users-modify

; Modules from the pre-v43 flat layout. They are stale copies of files that now
; live under src\ and must not survive an upgrade.
[InstallDelete]
Type: files; Name: "{app}\main.py"
Type: files; Name: "{app}\imports.py"
Type: files; Name: "{app}\test.py"
Type: filesandordirs; Name: "{app}\core"
Type: filesandordirs; Name: "{app}\views"
Type: filesandordirs; Name: "{app}\services"
Type: filesandordirs; Name: "{app}\pyncm"
Type: filesandordirs; Name: "{app}\__pycache__"
; Older builds left the launcher and its GIL-build python DLL in {app} or
; {app}\runtime. Either place breaks the app's free-threaded Python, so drop
; them; the new layout keeps everything under {app}\launcher.
Type: filesandordirs; Name: "{app}\runtime"
Type: files; Name: "{app}\Launch.bat"
Type: files; Name: "{app}\Launch.lnk"
Type: files; Name: "{app}\Launch.exe"
Type: files; Name: "{app}\python313.dll"
Type: files; Name: "{app}\python314.dll"
Type: files; Name: "{app}\select.pyd"
Type: files; Name: "{app}\unicodedata.pyd"
Type: files; Name: "{app}\_bz2.pyd"
Type: files; Name: "{app}\_ctypes.pyd"
Type: files; Name: "{app}\_decimal.pyd"
Type: files; Name: "{app}\_hashlib.pyd"
Type: files; Name: "{app}\_lzma.pyd"
Type: files; Name: "{app}\_socket.pyd"
Type: files; Name: "{app}\_ssl.pyd"
Type: files; Name: "{app}\_wmi.pyd"
Type: files; Name: "{app}\_zstd.pyd"
Type: files; Name: "{app}\vcruntime140.dll"
Type: files; Name: "{app}\vcruntime140_1.dll"
Type: files; Name: "{app}\libcrypto-3.dll"
Type: files; Name: "{app}\libcrypto-3-x64.dll"
Type: files; Name: "{app}\libffi-8.dll"
Type: files; Name: "{app}\libssl-3.dll"
Type: files; Name: "{app}\libssl-3-x64.dll"
Type: files; Name: "{app}\python3.dll"
Type: files; Name: "{app}\sqlite3.dll"
Type: files; Name: "{app}\pyexpat.pyd"
Type: files; Name: "{app}\winsound.pyd"

[Files]
; Launch.lnk holds absolute paths from the build machine, so it must not be
; installed; the [Icons] entries below create the real shortcuts with
; WorkingDir {app}.
Source: "build.result\raw\*"; DestDir: "{app}"; Excludes: "Launch.lnk"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExePath}"; IconFilename: "{app}\{#AppExePath}"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExePath}"; IconFilename: "{app}\{#AppExePath}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExePath}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent runasoriginaluser; Tasks: launchafter

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

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
