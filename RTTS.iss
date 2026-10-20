; Inno Setup script for RTTS. Build it with make_installer.bat (after build.bat).

[Setup]
AppId={{8F3A5C2E-6B1D-4E7A-9C40-2D5E7A1B3F68}
AppName=RTTS
AppVersion=1.0.0
AppPublisher=RTTS
; Installs just for the current user (no admin prompt). The folder stays writable,
; which RTTS needs to download its voice files next to RTTS.exe.
PrivilegesRequired=lowest
DefaultDirName={autopf}\RTTS
DefaultGroupName=RTTS
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=RTTS-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\RTTS.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "dist\RTTS\*"; DestDir: "{app}"; Excludes: "models,models\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\RTTS"; Filename: "{app}\RTTS.exe"
Name: "{autodesktop}\RTTS"; Filename: "{app}\RTTS.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RTTS.exe"; Description: "Launch RTTS"; Flags: nowait postinstall skipifsilent
Filename: "https://vb-audio.com/Cable/"; Description: "Open the VB-Cable download page (needed so Roblox can hear RTTS)"; Flags: shellexec postinstall skipifsilent unchecked

[UninstallDelete]
; the voice files RTTS downloaded after install
Type: filesandordirs; Name: "{app}\models"
