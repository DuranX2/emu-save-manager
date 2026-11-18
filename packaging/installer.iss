; Inno Setup 6 script - run via build_windows.bat (expects dist\EmuSaveManager from PyInstaller)
#define AppName "Emulator Save Manager"
#define AppExe  "EmuSaveManager.exe"
; The build passes the release tag (e.g. /DAppVer=1.2.0); this is the fallback.
#ifndef AppVer
  #define AppVer "1.0.0"
#endif

[Setup]
AppId={{6B1E5C7A-3C1D-4D0B-9B52-5E1F0A7D2C41}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher=DuranX2
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist
OutputBaseFilename=EmuSaveManager-Setup
Compression=lzma2
SolidCompression=yes
; Let the user choose "just for me" (no admin prompt) or all users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
SetupIconFile=art\emu-save-manager.ico

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\EmuSaveManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

; Settings and backups live in %USERPROFILE%\.emu_save_manager and are intentionally kept on uninstall.
