; Inno Setup script for RTTS. Build it with make_installer.bat (after build.bat).
; Change the version here when you release a new one.
#define AppVer "1.1.0"

[Setup]
AppId={{8F3A5C2E-6B1D-4E7A-9C40-2D5E7A1B3F68}
AppName=RTTS
AppVersion={#AppVer}
AppVerName=RTTS {#AppVer}
AppPublisher=RTTS
VersionInfoVersion={#AppVer}
; Installs just for the current user (no admin prompt). The folder stays writable,
; which RTTS needs to download its voice files next to RTTS.exe.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DefaultDirName={autopf}\RTTS
DefaultGroupName=RTTS
DisableProgramGroupPage=yes
DisableWelcomePage=no
LicenseFile=LICENSE
OutputDir=installer_output
OutputBaseFilename=RTTS-Setup-{#AppVer}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\RTTS.exe
; Tells Windows to reload icons when Setup finishes, so an update that changes RTTS's icon shows the new one
; on the desktop and Start menu shortcuts right away (instead of an old copy from the icon cache)
ChangesAssociations=yes
UninstallDisplayName=RTTS
Compression=lzma2
SolidCompression=yes
WizardStyle=modern dynamic
; The retro RTTS artwork: a tall picture on the Welcome / Finish pages and the icon at the top right of the
; other pages (each with a 200% copy for high-DPI screens; the small one is see-through for light and dark)
WizardImageFile=assets\installer\wizard-image-100.png,assets\installer\wizard-image-200.png
WizardSmallImageFile=assets\installer\wizard-small-100.png,assets\installer\wizard-small-200.png
; "dynamic" follows Windows dark mode, which has its own image settings: use the same artwork there
WizardImageFileDynamicDark=assets\installer\wizard-image-100.png,assets\installer\wizard-image-200.png
WizardSmallImageFileDynamicDark=assets\installer\wizard-small-100.png,assets\installer\wizard-small-200.png
; Updating: installing a new version over an old one closes RTTS first. Voices and settings are kept.
CloseApplications=yes
RestartApplications=no
; Needed to unpack the llama.cpp .zip that Orpheus uses
ArchiveExtraction=full

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nRTTS speaks what you type in Roblox chat through a virtual microphone, so you can use voice chat without talking.%n%nYou can pick voices to download now, or let RTTS download them later.
FinishedLabel=RTTS is installed.%n%nTo get started: in RTTS, set Output device to CABLE Input. Then in Roblox, set your microphone to CABLE Output. Press Start (F6) and type in chat.
FinishedLabelNoIcons=RTTS is installed.%n%nTo get started: in RTTS, set Output device to CABLE Input. Then in Roblox, set your microphone to CABLE Output. Press Start (F6) and type in chat.

[Types]
Name: "recommended"; Description: "Recommended: Kokoro Best"
Name: "everything"; Description: "Everything (includes ⚡ Orpheus, about 3 GB)"
Name: "novoices"; Description: "No voices now (RTTS downloads one when you first open it)"
Name: "custom"; Description: "Custom: pick your voices"; Flags: iscustom

[Components]
Name: "app"; Description: "RTTS (required)"; Types: recommended everything novoices custom; Flags: fixed
Name: "kokorobest"; Description: "Kokoro Best: very natural, best quality"; Types: recommended everything
Name: "kokorofast"; Description: "Kokoro Faster: very natural, lighter"; Types: everything
Name: "piperlow"; Description: "Piper Low: light and fast (Lessac)"; Types: everything
Name: "pipermedium"; Description: "Piper Medium: good all-rounder (Lessac, Alba, Alan)"; Types: everything
Name: "piperhigh"; Description: "Piper High: clearer (Ryan)"; Types: everything
Name: "orpheus"; Description: "⚡ Orpheus: most realistic, needs a strong graphics card"; Types: everything

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
; Off unless ticked. CurStepChanged hands the choice to RTTS, which you can change later in Settings.
Name: "voicestats"; Description: "Send anonymous stats about which voice I use (nothing I type is ever sent)"; GroupDescription: "Optional:"; Flags: unchecked

[Files]
Source: "dist\RTTS\*"; DestDir: "{app}"; Excludes: "models,models\*"; Components: app; Flags: ignoreversion recursesubdirs createallsubdirs

; Voices picked on the Select Components page download during the install, into the same models folder
; (with the same names, sizes and checksums) that RTTS.py uses, so the app sees them as downloaded.
; Files already there are skipped, so updates don't download them again. They're only removed on uninstall
; if the user says so.
Source: "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"; DestName: "voices-v1.0.bin"; DestDir: "{app}\models"; \
  Hash: "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"; ExternalSize: 28214398; \
  Components: kokorobest or kokorofast; Check: ModelMissing('voices-v1.0.bin', 28214398); Flags: external download ignoreversion uninsneveruninstall
Source: "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"; DestName: "kokoro-v1.0.onnx"; DestDir: "{app}\models"; \
  Hash: "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"; ExternalSize: 325532387; \
  Components: kokorobest; Check: ModelMissing('kokoro-v1.0.onnx', 325532387); Flags: external download ignoreversion uninsneveruninstall
Source: "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx"; DestName: "kokoro-v1.0.int8.onnx"; DestDir: "{app}\models"; \
  Hash: "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb"; ExternalSize: 92361271; \
  Components: kokorofast; Check: ModelMissing('kokoro-v1.0.int8.onnx', 92361271); Flags: external download ignoreversion uninsneveruninstall

Source: "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/low/en_US-lessac-low.onnx"; DestName: "en_US-lessac-low.onnx"; DestDir: "{app}\models"; \
  Hash: "f7d01dde371555732c4c314111ac79672b1a5ce2fc19266ab42178fd8df7f375"; ExternalSize: 63200000; \
  Components: piperlow; Check: ModelMissing('en_US-lessac-low.onnx', 0); Flags: external download ignoreversion uninsneveruninstall
Source: "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/low/en_US-lessac-low.onnx.json"; DestName: "en_US-lessac-low.onnx.json"; DestDir: "{app}\models"; \
  ExternalSize: 6000; Components: piperlow; Check: ModelMissing('en_US-lessac-low.onnx.json', 0); Flags: external download ignoreversion uninsneveruninstall

Source: "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx"; DestName: "en_US-lessac-medium.onnx"; DestDir: "{app}\models"; \
  ExternalSize: 63200000; Components: pipermedium; Check: ModelMissing('en_US-lessac-medium.onnx', 0); Flags: external download ignoreversion uninsneveruninstall
Source: "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"; DestName: "en_US-lessac-medium.onnx.json"; DestDir: "{app}\models"; \
  ExternalSize: 6000; Components: pipermedium; Check: ModelMissing('en_US-lessac-medium.onnx.json', 0); Flags: external download ignoreversion uninsneveruninstall
Source: "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alba/medium/en_GB-alba-medium.onnx"; DestName: "en_GB-alba-medium.onnx"; DestDir: "{app}\models"; \
  Hash: "401369c4a81d09fdd86c32c5c864440811dbdcc66466cde2d64f7133a66ad03b"; ExternalSize: 63200000; \
  Components: pipermedium; Check: ModelMissing('en_GB-alba-medium.onnx', 0); Flags: external download ignoreversion uninsneveruninstall
Source: "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alba/medium/en_GB-alba-medium.onnx.json"; DestName: "en_GB-alba-medium.onnx.json"; DestDir: "{app}\models"; \
  ExternalSize: 6000; Components: pipermedium; Check: ModelMissing('en_GB-alba-medium.onnx.json', 0); Flags: external download ignoreversion uninsneveruninstall
Source: "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx"; DestName: "en_GB-alan-medium.onnx"; DestDir: "{app}\models"; \
  ExternalSize: 63200000; Components: pipermedium; Check: ModelMissing('en_GB-alan-medium.onnx', 0); Flags: external download ignoreversion uninsneveruninstall
Source: "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json"; DestName: "en_GB-alan-medium.onnx.json"; DestDir: "{app}\models"; \
  ExternalSize: 6000; Components: pipermedium; Check: ModelMissing('en_GB-alan-medium.onnx.json', 0); Flags: external download ignoreversion uninsneveruninstall

Source: "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx"; DestName: "en_US-ryan-high.onnx"; DestDir: "{app}\models"; \
  Hash: "b3990d7606e183ec8dbfba70a4607074f162de1a0c412e0180d1ff60bb154eca"; ExternalSize: 121000000; \
  Components: piperhigh; Check: ModelMissing('en_US-ryan-high.onnx', 0); Flags: external download ignoreversion uninsneveruninstall
Source: "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx.json"; DestName: "en_US-ryan-high.onnx.json"; DestDir: "{app}\models"; \
  ExternalSize: 6000; Components: piperhigh; Check: ModelMissing('en_US-ryan-high.onnx.json', 0); Flags: external download ignoreversion uninsneveruninstall

Source: "https://huggingface.co/unsloth/orpheus-3b-0.1-ft-GGUF/resolve/e2b00302c46af8205f521f60016600aa25a068e6/orpheus-3b-0.1-ft-Q4_K_M.gguf"; DestName: "orpheus-3b-0.1-ft-Q4_K_M.gguf"; DestDir: "{app}\models"; \
  Hash: "4e94a925593c8fc1c20ee50e5b0e4bf915ea106a0bda3b917ff69b02559f7ab9"; ExternalSize: 2092569120; \
  Components: orpheus; Check: ModelMissing('orpheus-3b-0.1-ft-Q4_K_M.gguf', 2092569120); Flags: external download ignoreversion uninsneveruninstall
Source: "https://huggingface.co/onnx-community/snac_24khz-ONNX/resolve/f317cdff7efb7b2a63148c9e68bbb0320fe629a1/onnx/decoder_model.onnx"; DestName: "snac_24khz_decoder.onnx"; DestDir: "{app}\models"; \
  Hash: "9e2991fce96c3b49c508304e656052f4f6b860fe8432164fcd976eee195fd4f0"; ExternalSize: 52600822; \
  Components: orpheus; Check: ModelMissing('snac_24khz_decoder.onnx', 52600822); Flags: external download ignoreversion uninsneveruninstall
; llama.cpp is unpacked into the folder RTTS.py expects; CurStepChanged then writes the same ".ok" marker the app writes.
Source: "https://github.com/ggml-org/llama.cpp/releases/download/b11228/llama-b11228-bin-win-vulkan-x64.zip"; DestName: "llama-b11228-bin-win-vulkan-x64.zip"; DestDir: "{app}\models\llama.cpp-b11228-vulkan"; \
  Hash: "3b80cbb95b79b40967a4ec24592e32e966f50742d58042e777dc48862f0802d5"; ExternalSize: 33065094; \
  Components: orpheus; Check: LlamaMissing; Flags: external download extractarchive recursesubdirs ignoreversion uninsneveruninstall

[Icons]
Name: "{autoprograms}\RTTS"; Filename: "{app}\RTTS.exe"
Name: "{autodesktop}\RTTS"; Filename: "{app}\RTTS.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RTTS.exe"; Description: "Launch RTTS"; Flags: nowait postinstall skipifsilent
; RTTS's own updater runs this installer with /SILENT /RELAUNCH=1, so RTTS opens again when the update is done
Filename: "{app}\RTTS.exe"; Flags: nowait; Check: RelaunchAfterUpdate
Filename: "https://vb-audio.com/Cable/"; Description: "Open the VB-Cable download page (needed so Roblox can hear RTTS)"; Flags: shellexec postinstall skipifsilent; Check: not VBCableInstalled

[Code]
const
  VBCableKey = 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\VB:VBCABLE {87459874-1236-4469}';
  VBCableUrl = 'https://vb-audio.com/Cable/';
  LlamaDir = 'llama.cpp-b11228-vulkan';
  LlamaZip = 'llama-b11228-bin-win-vulkan-x64.zip';
  LlamaSha = '3b80cbb95b79b40967a4ec24592e32e966f50742d58042e777dc48862f0802d5';

var
  VBCablePage: TWizardPage;
  VBCableStatus: TNewStaticText;
  DeleteUserData: Boolean;

function RelaunchAfterUpdate: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

function VBCableInstalled: Boolean;
begin
  Result := RegKeyExists(HKLM64, VBCableKey) or RegKeyExists(HKLM32, VBCableKey)
    or DirExists(ExpandConstant('{commonpf64}\VB\CABLE'));
end;

function ModelsDir: String;
begin
  Result := AddBackslash(WizardDirValue) + 'models';
end;

{ Same test as remote_ready() in RTTS.py: the file is there with the right size (or, if its size isn't known, not empty). }
function ModelMissing(Name: String; Size: Integer): Boolean;
var
  N: Int64;
begin
  if not FileSize64(ModelsDir + '\' + Name, N) then
    Result := True
  else if Size > 0 then
    Result := N <> Size
  else
    Result := N = 0;
end;

function LlamaMarker: String;
begin
  Result := ModelsDir + '\' + LlamaDir + '\' + LlamaZip + '.ok';
end;

function LlamaMissing: Boolean;
begin
  Result := not FileExists(LlamaMarker);
end;

function OrpheusMissing: Boolean;
begin
  Result := ModelMissing('orpheus-3b-0.1-ft-Q4_K_M.gguf', 2092569120)
    or ModelMissing('snac_24khz_decoder.onnx', 52600822) or LlamaMissing;
end;

procedure OpenVBCableClick(Sender: TObject);
var
  Code: Integer;
begin
  ShellExec('open', VBCableUrl, '', '', SW_SHOWNORMAL, ewNoWait, Code);
end;

procedure UpdateVBCableStatus;
begin
  if VBCableInstalled then
    VBCableStatus.Caption := 'VB-Cable is installed. Click Next to continue.'
  else
    VBCableStatus.Caption := 'VB-Cable was not found yet. You can also install it after RTTS.';
end;

procedure CheckAgainClick(Sender: TObject);
begin
  UpdateVBCableStatus;
end;

procedure InitializeWizard;
var
  Info: TNewStaticText;
  OpenButton, CheckButton: TNewButton;
begin
  VBCablePage := CreateCustomPage(wpLicense, 'VB-Cable is needed',
    'RTTS uses VB-Cable as a virtual microphone so Roblox can hear it.');

  Info := TNewStaticText.Create(VBCablePage);
  Info.Parent := VBCablePage.Surface;
  Info.AutoSize := False;
  Info.WordWrap := True;
  Info.Width := VBCablePage.SurfaceWidth;
  Info.Height := ScaleY(84);
  Info.Caption := 'VB-Cable is free and isn''t installed on this PC yet. Without it, RTTS can''t speak in Roblox voice chat.' + #13#10#13#10 +
    'Download it, run its setup as administrator, and restart your PC when it asks. ' +
    'You can keep installing RTTS now and set up VB-Cable afterwards.';

  OpenButton := TNewButton.Create(VBCablePage);
  OpenButton.Parent := VBCablePage.Surface;
  OpenButton.Top := Info.Top + Info.Height + ScaleY(8);
  OpenButton.Width := ScaleX(230);
  OpenButton.Height := ScaleY(26);
  OpenButton.Caption := 'Open the VB-Cable download page';
  OpenButton.OnClick := @OpenVBCableClick;

  CheckButton := TNewButton.Create(VBCablePage);
  CheckButton.Parent := VBCablePage.Surface;
  CheckButton.Top := OpenButton.Top;
  CheckButton.Left := OpenButton.Left + OpenButton.Width + ScaleX(8);
  CheckButton.Width := ScaleX(100);
  CheckButton.Height := OpenButton.Height;
  CheckButton.Caption := 'Check again';
  CheckButton.OnClick := @CheckAgainClick;

  VBCableStatus := TNewStaticText.Create(VBCablePage);
  VBCableStatus.Parent := VBCablePage.Surface;
  VBCableStatus.AutoSize := False;
  VBCableStatus.WordWrap := True;
  VBCableStatus.Width := VBCablePage.SurfaceWidth;
  VBCableStatus.Height := ScaleY(30);
  VBCableStatus.Top := OpenButton.Top + OpenButton.Height + ScaleY(12);
  UpdateVBCableStatus;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  { Only shown when VB-Cable isn't installed when setup starts. }
  Result := (VBCablePage <> nil) and (PageID = VBCablePage.ID) and VBCableInstalled;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  { Large models always warn before their first download, but the user can still go ahead (same as in the app). }
  if (CurPageID = wpSelectComponents) and WizardIsComponentSelected('orpheus') and OrpheusMissing then
    Result := SuppressibleMsgBox(
      '⚡ Orpheus is a large model (3 billion parameters, a 2.2 GB download).' + #13#10#13#10 +
      'It sounds the most human, but it needs a strong PC:' + #13#10 +
      '  - An NVIDIA graphics card with 8 GB of video memory or more is recommended.' + #13#10 +
      '  - On lower-end PCs it is slow, and it can lower your FPS in Roblox while it speaks.' + #13#10#13#10 +
      'Are you sure you want to download Orpheus? (Choose No to go back and change your voices.)',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDYES) = IDYES;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  { Mark llama.cpp as unpacked, like RTTS.py does after it unpacks the .zip itself. }
  if (CurStep = ssPostInstall) and LlamaMissing
    and FileExists(ModelsDir + '\' + LlamaDir + '\llama-server.exe') then
    SaveStringToFile(LlamaMarker, LlamaSha, False);
  { Voice stats: RTTS reads this file once on its next start, saves the choice in its settings and deletes it.
    Silent installs (RTTS's own updater) don't write it, so an update never changes the choice made in Settings. }
  if (CurStep = ssPostInstall) and not WizardSilent then begin
    ForceDirectories(ExpandConstant('{userappdata}\RTTS'));
    if WizardIsTaskSelected('voicestats') then
      SaveStringToFile(ExpandConstant('{userappdata}\RTTS\voice_stats_choice.txt'), '1', False)
    else
      SaveStringToFile(ExpandConstant('{userappdata}\RTTS\voice_stats_choice.txt'), '0', False);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    DeleteUserData := (not UninstallSilent) and (MsgBox(
      'Do you also want to delete the voices RTTS downloaded and your RTTS settings?' + #13#10#13#10 +
      'Choose No to keep them, so you don''t have to download the voices again if you reinstall RTTS.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES);
  if (CurUninstallStep = usPostUninstall) and DeleteUserData then begin
    DelTree(ExpandConstant('{app}\models'), True, True, True);
    DelTree(ExpandConstant('{userappdata}\RTTS'), True, True, True);
    RemoveDir(ExpandConstant('{app}'));
  end;
end;
