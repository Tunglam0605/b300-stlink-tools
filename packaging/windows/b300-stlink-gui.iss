#ifndef SourceRoot
  #error SourceRoot must point to the extracted Windows native bundle
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{B300-STLINK-GUI-0605}}
AppName=B300 ST-Link Provisioning
AppVersion={#AppVersion}
AppPublisher=TungLamAutomation
DefaultDirName={localappdata}\B300-STLink
DefaultGroupName=B300 ST-Link
OutputDir={#OutputDir}
OutputBaseFilename=B300-STLink-GUI-Setup-{#AppVersion}-windows-x64
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#SourceRoot}\LICENSE
SetupIconFile={#SourceRoot}\b300-stlink-icon.ico
UninstallDisplayIcon={app}\b300-stlink-gui.exe

[Files]
Source: "{#SourceRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\B300 ST-Link Provisioning"; Filename: "{app}\b300-stlink-gui.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\B300 ST-Link Provisioning"; Filename: "{app}\b300-stlink-gui.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\b300-stlink-gui.exe"; Description: "Launch B300 ST-Link Provisioning"; Flags: nowait postinstall skipifsilent
