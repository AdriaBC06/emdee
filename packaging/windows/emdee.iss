; SPDX-License-Identifier: GPL-3.0-or-later
; Inno Setup script for Emdee.
;
;   iscc packaging\windows\emdee.iss
;
; Expects dist\Emdee\ to exist — build it first with:
;   pyinstaller packaging\windows\emdee.spec --noconfirm
;
; Installs per-user by default. Emdee needs nothing outside the user's own
; profile, and a per-user install means no UAC prompt, which matters for an
; unsigned binary: an elevation dialog for an application Windows cannot vouch
; for is a worse experience than no elevation at all.

#define AppName "Emdee"
#define AppVersion "1.0.0"
#define AppPublisher "Adrià Bonnin Catalán"
#define AppURL "https://github.com/AdriaBC06/emdee"
#define AppExe "Emdee.exe"
#define SourceDir "..\..\dist\Emdee"

[Setup]
AppId={{7C4F2E10-9B3A-4D6E-8A21-5E9C1F0B7D33}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}

; Per-user install: no administrator rights, no UAC prompt.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes

LicenseFile=..\..\LICENSE
OutputDir=..\..\dist
OutputBaseFilename=Emdee-{#AppVersion}-setup
SetupIconFile=emdee.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}

; The payload is mostly Chromium and compresses slowly but very well.
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

; Makes Inno notify the shell (SHChangeNotify with SHCNE_ASSOCCHANGED) once the
; registry keys below are written, so Explorer picks the new handler up without
; the user signing out.
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "associate"; Description: "Open &Markdown files (.md, .markdown) with {#AppName}"; \
    GroupDescription: "File associations:"; Flags: unchecked

[Files]
; The whole PyInstaller folder, recursively. recursesubdirs plus createallsubdirs
; keeps _internal's tree intact — QtWebEngineProcess.exe resolves its resources
; by relative path, so a flattened install would start and then never render a
; preview.
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Registered under a private ProgID rather than seized as the default handler:
; the association shows up in "Open with" and in Default Apps, and Windows asks
; the user before switching. Taking the default outright is both rude and, since
; Windows 10, silently reverted.
Root: HKCU; Subkey: "Software\Classes\Emdee.Markdown"; \
    ValueType: string; ValueName: ""; ValueData: "Markdown document"; \
    Flags: uninsdeletekey; Tasks: associate
Root: HKCU; Subkey: "Software\Classes\Emdee.Markdown\DefaultIcon"; \
    ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; \
    Flags: uninsdeletekey; Tasks: associate
Root: HKCU; Subkey: "Software\Classes\Emdee.Markdown\shell\open\command"; \
    ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; \
    Flags: uninsdeletekey; Tasks: associate

Root: HKCU; Subkey: "Software\Classes\.md\OpenWithProgids"; \
    ValueType: string; ValueName: "Emdee.Markdown"; ValueData: ""; \
    Flags: uninsdeletevalue; Tasks: associate
Root: HKCU; Subkey: "Software\Classes\.markdown\OpenWithProgids"; \
    ValueType: string; ValueName: "Emdee.Markdown"; ValueData: ""; \
    Flags: uninsdeletevalue; Tasks: associate

; Lets Emdee appear in "Open with" and in Settings > Default apps as a real
; application.
;
; The parent key is listed in its own right so that uninstalling removes the
; whole subtree. uninsdeletekey removes only the key it is written against, so
; tagging just the leaves below would strip their values and leave a trail of
; empty Applications\Emdee.exe\shell\open husks in the registry.
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}"; \
    Flags: uninsdeletekey; Tasks: associate
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; \
    ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; \
    Tasks: associate
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; \
    ValueType: string; ValueName: ".md"; ValueData: ""; \
    Tasks: associate
Root: HKCU; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; \
    ValueType: string; ValueName: ".markdown"; ValueData: ""; \
    Tasks: associate

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
    Flags: nowait postinstall skipifsilent

[Code]
{ Making Emdee the default handler for .md needs more care than a [Registry]
  line. Writing the value is easy; the problem is uninstalling. A plain
  uninsdeletevalue would remove our ProgID and leave the extension with *no*
  default at all, even if something else owned it before we arrived — so
  uninstalling Emdee would quietly break whatever the user had.

  So the previous value is stashed under our own key first and put back on the
  way out. Windows' own UserChoice, when the user has explicitly picked an
  application in Settings, still outranks all of this; that is deliberate, and
  means ticking this box can never override a choice the user made on purpose. }

const
  BackupKey = 'Software\Emdee\AssociationBackup';

procedure ClaimExtension(Ext: string);
var
  Previous: string;
begin
  if not RegQueryStringValue(HKCU, 'Software\Classes\' + Ext, '', Previous) then
    Previous := '';
  RegWriteStringValue(HKCU, BackupKey, Ext, Previous);
  RegWriteStringValue(HKCU, 'Software\Classes\' + Ext, '', 'Emdee.Markdown');
end;

procedure ReleaseExtension(Ext: string);
var
  Previous, Current: string;
begin
  { Only give the extension back if we are still the one holding it: the user
    may have reassigned it since installing, and stamping the old value over
    their newer choice would be worse than doing nothing. }
  if not RegQueryStringValue(HKCU, 'Software\Classes\' + Ext, '', Current) then
    Current := '';
  if Current <> 'Emdee.Markdown' then
    Exit;

  if RegQueryStringValue(HKCU, BackupKey, Ext, Previous) and (Previous <> '') then
    RegWriteStringValue(HKCU, 'Software\Classes\' + Ext, '', Previous)
  else
    RegDeleteValue(HKCU, 'Software\Classes\' + Ext, '');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('associate') then
  begin
    ClaimExtension('.md');
    ClaimExtension('.markdown');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    ReleaseExtension('.md');
    ReleaseExtension('.markdown');
    RegDeleteKeyIncludingSubkeys(HKCU, BackupKey);
    RegDeleteKeyIfEmpty(HKCU, 'Software\Emdee');
  end;
end;

[UninstallDelete]
; Chromium's cache and Emdee's recoloured stylesheet icons are written after
; installation, so the installer has no record of them and would otherwise leave
; the directories behind. The preferences file in %APPDATA% is deliberately not
; touched: settings should survive a reinstall, which is what every other
; application does and what a user reinstalling to fix something expects.
Type: filesandordirs; Name: "{localappdata}\Emdee\Emdee\cache"
Type: dirifempty; Name: "{localappdata}\Emdee\Emdee"
Type: dirifempty; Name: "{localappdata}\Emdee"
