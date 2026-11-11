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
;
; Its look - Windows 11's controls, light or dark to match Windows, the app's
; colours and its mark - needs Inno Setup 6.7 or newer; the pictures come from
; tools/make_installer_art.py.

#if Ver < EncodeVer(6, 7, 0)
  #error Inno Setup 6.7 or newer is needed: the wizard's colours and dark mode use settings added in 6.6 and 6.7
#endif

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
; Windows 11's controls, light or dark to match Windows (read once, as Setup
; starts), on the app's own background colours - design_system/tokens.py's
; light #f6f7f9 and dark #171a21 - with no divider lines. Setting a colour makes
; every panel transparent, so each page is one surface. In a high-contrast
; theme Inno leaves all of this off and Windows' own colours win.
WizardStyle=modern dynamic windows11 hidebevels
WizardBackColor=#f6f7f9
WizardBackColorDynamicDark=#171a21
; Pictures from tools/make_installer_art.py, one per Windows scale from 100% to
; 250% so none is stretched. The last page's panel is transparent over its own
; colour - the app's dark surface, darker still on a dark page - so one set
; serves both. The corner mark follows widgets/core/mark.py: the text colour on
; a light page, white on a dark one.
WizardImageFile=wizard\side-202.png,wizard\side-269.png,wizard\side-336.png,wizard\side-403.png,wizard\side-430.png,wizard\side-498.png,wizard\side-534.png
WizardImageBackColor=#171a21
WizardImageBackColorDynamicDark=#0e1014
WizardSmallImageFile=wizard\corner-58.png,wizard\corner-77.png,wizard\corner-97.png,wizard\corner-116.png,wizard\corner-124.png,wizard\corner-143.png,wizard\corner-159.png
WizardSmallImageFileDynamicDark=wizard\corner-dark-58.png,wizard\corner-dark-77.png,wizard\corner-dark-97.png,wizard\corner-dark-116.png,wizard\corner-dark-124.png,wizard\corner-dark-143.png,wizard\corner-dark-159.png
WizardSmallImageBackColor=none
WizardSmallImageBackColorDynamicDark=none
; The mark on a dark tile. The app's own icon is the bare white mark, which
; vanishes on a light title bar and on Setup.exe in a light Explorer window.
SetupIconFile=wizard\setup.ico
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
; Settings -> Start with Windows and Desktop shortcut change the first two
; later, which the first page says. The third opens the app on Finish (see [Run]).
Name: "startup"; Description: "Start PC Widgets when you sign in"
Name: "desktopicon"; Description: "Add a shortcut to the desktop"; Flags: unchecked
Name: "launch"; Description: "Open PC Widgets when it's installed"

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
; Runs on Finish, when the first page's "Open PC Widgets when it's installed" is
; ticked, so the app opens as Setup closes and whatever it shows first has the
; screen to itself. The last page's own tick box for it is hidden (CurPageChanged
; below): the choice was already made. Never in a silent install.
Filename: "{app}\{#AppExe}"; Description: "Open PC Widgets"; Flags: nowait postinstall skipifsilent; Tasks: launch

[Messages]
; Plain words instead of installer jargon ("Completing the ... Setup Wizard",
; "Click Finish to exit Setup", "Select the additional tasks you would like
; Setup to perform"). Kept to ASCII, like everything in this file that Setup
; shows, since the file's encoding isn't marked.
SetupWindowTitle=%1 Setup
WizardSelectTasks=Install [name]
SelectTasksDesc=Desktop widgets for Windows, by {#AppPublisher}
SelectTasksLabel2=[name] installs just for you, so it doesn't need an administrator. You can change the first two later in its settings.
WizardInstalling=Installing [name]
InstallingLabel=This usually takes less than a minute.
StatusExtractFiles=Copying files...
FinishedHeadingLabel=[name] is installed
FinishedLabel=It's in the Start menu, and your widgets appear on the desktop as soon as it opens.
FinishedLabelNoIcons=It's in the Start menu, and your widgets appear on the desktop as soon as it opens.
ClickFinish=

[CustomMessages]
FinishedOpens=It's in the Start menu, and it opens when you select Finish.

[Code]
procedure InitializeWizard;
begin
  { While copying, the progress bar and "Copying files..." say enough; the
    path of each file in _internal is noise. }
  WizardForm.FilenameLabel.Visible := False;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { The choices page is the last before installing (the Ready page is off), so
    its button installs - and says so, instead of Inno's "Next". }
  if CurPageID = wpSelectTasks then
    WizardForm.NextButton.Caption := SetupMessage(msgButtonInstall);
  if CurPageID = wpFinished then
  begin
    { Whether to open it was asked on the first page, so not again here; the
      ticked [Run] entry still runs on Finish. The wording says what happens. }
    WizardForm.RunList.Visible := False;
    if WizardIsTaskSelected('launch') then
      WizardForm.FinishedLabel.Caption := CustomMessage('FinishedOpens');
  end;
end;

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
