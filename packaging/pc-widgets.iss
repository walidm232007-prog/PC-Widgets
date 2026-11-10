; Inno Setup script for PC-Widgets-Setup-<version>.exe. tools/build_exe.py
; --installer compiles it after building the exe; packaging/README.md explains.
;
;   ISCC /DAppVersion=1.0.0 /DAppPublisher=<name> /DAppCopyright=<line>
;        /DAppURL=<repo> /DSourceDir=<dist\PC Widgets> /DOutputDir=<out> pc-widgets.iss
;
; The version and the publisher come from widgets/version.py, which
; tools/build_exe.py reads and passes in, so the exe and the installer can't
; disagree. As arguments they also keep the "©" out of this file's encoding.
;
; Per user, no admin: installs to %LOCALAPPDATA%\Programs\PC Widgets, and its
; shortcuts are the ones the app itself makes (widgets/core/startup.py), at the
; same paths, so the app's own "start with Windows" switch and the installer's
; agree about which shortcut is which.

#ifndef AppVersion
  #error Pass /DAppVersion=<version>
#endif
#ifndef AppPublisher
  #error Pass /DAppPublisher=<name> (tools/build_exe.py reads it from widgets/version.py)
#endif
#ifndef AppCopyright
  #error Pass /DAppCopyright=<line>
#endif
#ifndef AppURL
  #error Pass /DAppURL=<the project's page>
#endif
#ifndef AppVersionNumbers
  ; The installer's own file version wants four numbers.
  #define AppVersionNumbers AppVersion
#endif
#ifndef SourceDir
  #error Pass /DSourceDir=<the built "PC Widgets" folder>
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

#define AppName "PC Widgets"
#define AppExe "PC Widgets.exe"

[Setup]
; Never change AppId: it is how an update finds the copy it replaces.
AppId={{5B0C7E62-3A1D-4F7B-9C8E-2D6A4E1F9B30}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
; What Settings -> Apps shows as the publisher and its links, and what the
; installer's own properties say.
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
AppCopyright={#AppCopyright}
VersionInfoVersion={#AppVersionNumbers}
VersionInfoTextVersion={#AppVersion}
VersionInfoProductTextVersion={#AppVersion}
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
SetupIconFile=..\assets\widgets.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir={#OutputDir}
OutputBaseFilename=PC-Widgets-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
; An update or an uninstall while the board runs: Windows' Restart Manager
; closes it, which also ends its taskbar guard, so the taskbar comes back.
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "startup"; Description: "Start PC Widgets with Windows"
Name: "desktopicon"; Description: "Put a shortcut on the desktop"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; An update replaces the bundle whole: files a newer build no longer has must
; not linger and be loaded instead of the new ones. The same for the licence
; texts beside the exe: a notice for something a newer build no longer ships
; would claim a component that isn't there.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\licenses"

[UninstallDelete]
; Made after install, so the uninstaller has no record of it: the launcher that
; `PC Widgets.exe --app` puts in the program folder.
Type: files; Name: "{app}\{#AppName}.lnk"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Desktop widgets"
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--background"; WorkingDir: "{app}"; Comment: "Desktop widgets"; Tasks: startup
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Desktop widgets"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open PC Widgets"; Flags: nowait postinstall skipifsilent

[Code]
{ The app can make the same shortcuts itself after install (Settings, the tray
  menu), and the uninstaller only knows about the ones it made. So on uninstall
  every "PC Widgets" shortcut in those three places that points into this
  install goes - and one that points anywhere else, such as a copy run from a
  checkout with Python, is left alone. }
procedure RemoveOwnShortcut(Path: String);
var
  Shell, Link: Variant;
  Target: String;
begin
  if not FileExists(Path) then
    Exit;
  try
    Shell := CreateOleObject('WScript.Shell');
    Link := Shell.CreateShortcut(Path);
    Target := Link.TargetPath;
    if Pos(Lowercase(ExpandConstant('{app}\')), Lowercase(Target)) = 1 then
      DeleteFile(Path);
  except
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    RemoveOwnShortcut(ExpandConstant('{userstartup}\{#AppName}.lnk'));
    RemoveOwnShortcut(ExpandConstant('{autodesktop}\{#AppName}.lnk'));
    RemoveOwnShortcut(ExpandConstant('{autoprograms}\{#AppName}.lnk'));
  end;
end;
