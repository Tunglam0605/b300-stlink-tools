#ifndef SourceRoot
  #error SourceRoot must point to the extracted Windows native bundle
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif
#ifndef AppVersion
  #error AppVersion must be passed from b300_version.py with /DAppVersion
#endif

[Setup]
AppId={{B300-STLINK-GUI-0605}}
AppName=B300 ST-Link Tools
AppVersion={#AppVersion}
AppPublisher=TungLamAutomation
DefaultDirName={localappdata}\B300-STLink
DefaultGroupName=B300 ST-Link Tools
OutputDir={#OutputDir}
OutputBaseFilename=B300-STLink-GUI-Windows-x64
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no
LicenseFile={#SourceRoot}\LICENSE
SetupIconFile={#SourceRoot}\b300-stlink-icon.ico
UninstallDisplayIcon={app}\b300-stlink-gui.exe

[InstallDelete]
; PyInstaller onedir files are one private, version-coupled runtime. Remove the
; previous owned trees before extraction so obsolete .pyc/.pyd/DLL files cannot
; survive an upgrade and mix Python runtimes. Never wildcard-delete {app}.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\vendor"

[Files]
Source: "{#SourceRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\B300 ST-Link Tools"; Filename: "{app}\b300-stlink-gui.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\B300 ST-Link Tools"; Filename: "{app}\b300-stlink-gui.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\b300-stlink-gui.exe"; Parameters: "--first-run-setup"; Description: "Mở B300 ST-Link Tools và tự chuẩn bị máy"; Flags: nowait postinstall skipifsilent
