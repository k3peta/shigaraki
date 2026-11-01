; KoeKichi for Windows installer.
; Build with:
;   iscc /DSourceDir=dist\KoeKichiWin /DOutputDir=dist /DAppVersion=0.1.0 installers\windows\KoeKichiWin.iss

#ifndef AppVersion
#define AppVersion "0.0.0"
#endif

#ifndef SourceDir
#define SourceDir "..\..\dist\KoeKichiWin"
#endif

#ifndef OutputDir
#define OutputDir "..\..\dist"
#endif

#ifndef IconFile
#define IconFile "..\..\assets\koe-kichi.ico"
#endif

[Setup]
AppId={{8E3B7D6C-1D7D-4A1E-8F68-4D58E4F0D8A1}
AppName=KoeKichi
AppVersion={#AppVersion}
AppPublisher=k3peta
AppPublisherURL=https://github.com/k3peta/koekichi-win
AppSupportURL=https://github.com/k3peta/koekichi-win/issues
AppUpdatesURL=https://github.com/k3peta/koekichi-win/releases
DefaultDirName={localappdata}\KoeKichi\app
DefaultGroupName=Koe Kichi
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=KoeKichiWin-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\KoeKichiWin.exe
LicenseFile=..\..\LICENSE
SetupIconFile={#IconFile}

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\README-Windows.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Koe Kichi"; Filename: "{app}\KoeKichiWin.exe"; Parameters: "run"; WorkingDir: "{app}"
Name: "{group}\Koe Kichi Settings"; Filename: "{app}\KoeKichiWin.exe"; Parameters: "configure"; WorkingDir: "{app}"
Name: "{group}\Koe Kichi Setup Check"; Filename: "{app}\KoeKichiWin.exe"; Parameters: "setup --model-timeout-seconds 900"; WorkingDir: "{app}"
Name: "{group}\Koe Kichi Diagnose"; Filename: "{app}\KoeKichiWin.exe"; Parameters: "diagnose"; WorkingDir: "{app}"

[Run]
Filename: "{app}\KoeKichiWin.exe"; Parameters: "init"; Flags: runhidden waituntilterminated
Filename: "{app}\KoeKichiWin.exe"; Parameters: "run"; Description: "Launch Koe Kichi"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
