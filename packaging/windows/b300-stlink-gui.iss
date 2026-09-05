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
CreateUninstallRegKey=not B300IsolatedTest

[InstallDelete]
; The old owned runtime has already been moved into the rollback directory by
; ssInstall. Never depend on Inno restoring files removed by InstallDelete.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\vendor"

[Files]
; Extract and verify the complete embedded payload before moving the old tree.
Source: "{#SourceRoot}\*"; DestDir: "{tmp}\b300-stage"; Flags: dontcopy recursesubdirs createallsubdirs
; CI-only fault injection: this external payload is intentionally absent. It is
; selected only by the private rollback switch, after [InstallDelete] has run,
; so silent Setup must cancel and restore the previous owned runtime tree.
Source: "{tmp}\b300-failed-upgrade-payload.invalid"; DestDir: "{app}"; Flags: external; Check: B300FailureInjectionRequested
Source: "{#SourceRoot}\*"; DestDir: "{app}"; Excludes: "B300-RUNTIME.sha256"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\B300-RUNTIME.sha256"; DestDir: "{app}"; Flags: ignoreversion; AfterInstall: B300ValidateInstalled
Source: "{tmp}\b300-failed-upgrade-payload.invalid"; DestDir: "{app}"; Flags: external; Check: B300LateFailureInjectionRequested

[Icons]
Name: "{group}\B300 ST-Link Tools"; Filename: "{app}\b300-stlink-gui.exe"; WorkingDir: "{app}"; Check: not B300IsolatedTest
Name: "{autodesktop}\B300 ST-Link Tools"; Filename: "{app}\b300-stlink-gui.exe"; WorkingDir: "{app}"; Tasks: desktopicon; Check: not B300IsolatedTest

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\b300-stlink-gui.exe"; Parameters: "--first-run-setup"; Description: "Mở B300 ST-Link Tools và tự chuẩn bị máy"; Flags: nowait postinstall skipifsilent

[Code]
var
  OwnedPaths, MovedPaths: TStringList;
  BackupRoot: String;
  InstallSucceeded, BackupCreated: Boolean;

function B300IsolatedTest: Boolean;
begin
  Result := CompareText(ExpandConstant('{param:B300TESTISOLATED|0}'), '1') = 0;
end;

function B300FailureInjectionRequested: Boolean;
begin
  Result := CompareText(ExpandConstant('{param:B300TESTFAILUPGRADE|0}'), '1') = 0;
end;

function B300LateFailureInjectionRequested: Boolean;
begin
  Result := CompareText(ExpandConstant('{param:B300TESTFAILUPGRADE|0}'), 'late') = 0;
end;

procedure B300CheckCoverage(Root, Relative: String; Listed: TStringList);
var
  Entry: TFindRec;
  Name: String;
begin
  if FindFirst(Root + '\' + Relative + '*', Entry) then begin
    try
      repeat
        if (Entry.Name <> '.') and (Entry.Name <> '..') then begin
          Name := Relative + Entry.Name;
          if Entry.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then
            B300CheckCoverage(Root, Name + '\', Listed)
          else if (Name <> 'B300-RUNTIME.sha256') and (Listed.IndexOf(Name) < 0) then
            RaiseException('Unlisted B300 payload file: ' + Name);
        end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
end;

procedure B300ValidateRuntime(Root: String; ExactCoverage: Boolean);
var
  Lines: TArrayOfString;
  Listed: TStringList;
  I: Integer;
  Name, Digest: String;
begin
  if not LoadStringsFromFile(Root + '\B300-RUNTIME.sha256', Lines) then
    RaiseException('B300 runtime integrity manifest missing');
  if GetArrayLength(Lines) < 2 then
    RaiseException('B300 runtime integrity manifest is empty');
  if Lines[0] <> '# B300 runtime {#AppVersion}' then
    RaiseException('B300 runtime version does not match installer');
  Listed := TStringList.Create;
  try
    for I := 1 to GetArrayLength(Lines) - 1 do begin
      if (Length(Lines[I]) < 67) or (Copy(Lines[I], 65, 2) <> ' *') then
        RaiseException('Invalid B300 runtime manifest entry');
      Digest := Copy(Lines[I], 1, 64);
      Name := Copy(Lines[I], 67, Length(Lines[I]));
      if (Pos(':', Name) > 0) or (Pos('..', Name) > 0) or
         (Copy(Name, 1, 1) = '/') or (Pos('\', Name) > 0) then
        RaiseException('Unsafe B300 runtime manifest path');
      StringChangeEx(Name, '/', '\', True);
      if Listed.IndexOf(Name) >= 0 then
        RaiseException('Duplicate B300 runtime manifest path');
      Listed.Add(Name);
      if not FileExists(Root + '\' + Name) then
        RaiseException('B300 runtime file missing: ' + Name);
      if CompareText(GetSHA256OfFile(Root + '\' + Name), Digest) <> 0 then
        RaiseException('B300 runtime hash mismatch: ' + Name);
    end;
    if Listed.IndexOf('b300-stlink-gui.exe') < 0 then
      RaiseException('B300 runtime executable is unlisted');
    if ExactCoverage then B300CheckCoverage(Root, '', Listed);
  finally
    Listed.Free;
  end;
end;

procedure B300ValidateInstalled;
begin
  B300ValidateRuntime(ExpandConstant('{app}'), False);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  BackupRoot := ExpandConstant('{app}\.b300-upgrade-rollback');
  if DirExists(BackupRoot) then begin
    Result := 'A previous upgrade backup requires recovery: ' + BackupRoot;
    exit;
  end;
  try
    ExtractTemporaryFiles('{tmp}\b300-stage\*');
    B300ValidateRuntime(ExpandConstant('{tmp}\b300-stage'), True);
  except
    Result := 'Cannot stage the complete B300 runtime: ' + GetExceptionMessage;
  end;
end;

procedure InitializeWizard;
begin
  OwnedPaths := TStringList.Create;
  MovedPaths := TStringList.Create;
  { Exclusive runtime directories and publisher-supplied files only. Settings,
    logs, caches and user files elsewhere in app are never swept. }
  OwnedPaths.Add('_internal');
  OwnedPaths.Add('vendor');
  OwnedPaths.Add('resources\firmware');
  OwnedPaths.Add('b300-stlink-gui.exe');
  OwnedPaths.Add('LICENSE');
  OwnedPaths.Add('BUNDLE-METADATA.txt');
  OwnedPaths.Add('B300-RUNTIME.sha256');
  OwnedPaths.Add('install.ps1');
  OwnedPaths.Add('b300-stlink-icon.ico');
  OwnedPaths.Add('b300-stlink-icon.png');
  OwnedPaths.Add('b300-stlink-wordmark.png');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  I: Integer;
  OldPath, SavedPath: String;
begin
  if CurStep = ssInstall then begin
    if not ForceDirectories(BackupRoot) then
      RaiseException('Cannot create B300 runtime backup');
    BackupCreated := True;
    for I := 0 to OwnedPaths.Count - 1 do begin
      OldPath := ExpandConstant('{app}\') + OwnedPaths[I];
      SavedPath := BackupRoot + '\' + OwnedPaths[I];
      if FileExists(OldPath) or DirExists(OldPath) then begin
        if not ForceDirectories(ExtractFileDir(SavedPath)) then
          RaiseException('Cannot prepare B300 runtime backup');
        if not RenameFile(OldPath, SavedPath) then
          RaiseException('Close B300 and its tools before upgrading: ' + OldPath);
      end;
      MovedPaths.Add(OwnedPaths[I]);
    end;
  end;
  if CurStep = ssDone then
    InstallSucceeded := True;
end;

procedure DeinitializeSetup;
var
  I: Integer;
  OldPath, SavedPath: String;
  Restored: Boolean;
begin
  if MovedPaths = nil then exit;
  if not BackupCreated then begin
    MovedPaths.Free;
    OwnedPaths.Free;
    exit;
  end;
  Restored := True;
  if not InstallSucceeded then begin
    { Inno has finished its own rollback by this event. Restore afterwards so
      its file undo cannot delete the old files we have just recovered. }
    for I := MovedPaths.Count - 1 downto 0 do begin
      OldPath := ExpandConstant('{app}\') + MovedPaths[I];
      SavedPath := BackupRoot + '\' + MovedPaths[I];
      if DirExists(OldPath) then
        Restored := DelTree(OldPath, True, True, True) and Restored
      else if FileExists(OldPath) then
        Restored := DeleteFile(OldPath) and Restored;
      if FileExists(SavedPath) or DirExists(SavedPath) then begin
        ForceDirectories(ExtractFileDir(OldPath));
        if not RenameFile(SavedPath, OldPath) then Restored := False;
      end;
    end;
  end;
  if Restored and (BackupRoot <> '') then begin
    if not DelTree(BackupRoot, True, True, True) then
      Log('B300 backup cleanup pending: ' + BackupRoot);
  end else if not Restored then
    SuppressibleMsgBox('B300 rollback needs recovery. Preserved backup: ' + BackupRoot,
      mbError, MB_OK, IDOK);
  MovedPaths.Free;
  OwnedPaths.Free;
end;
