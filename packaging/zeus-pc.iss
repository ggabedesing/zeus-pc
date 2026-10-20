; Zeus PC — instalador por usuário (Increment 023 — packaging).
; Compilação (a partir da raiz do repositório):
;   "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" packaging\zeus-pc.iss
; Gera dist\ZeusPC-Setup-0.1.0.exe.
;
; PrivilegesRequired=lowest -> sem UAC: instala em {localappdata}\Programs\ZeusPC
; e cria atalhos apenas para o usuário atual. Os dados ({localappdata}\ZeusPC\data)
; NÃO são apagados na desinstalação: são o histórico de análises da máquina.

#define MyAppName "Zeus PC"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Zeus PC"
#define MyAppExeName "ZeusPC.exe"

[Setup]
AppId={{7C4E1F2A-9B3D-4E68-A2C5-5D8F0B1E6A41}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\ZeusPC
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=ZeusPC-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=zeus.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos:"; Flags: unchecked
Name: "startup"; Description: "Iniciar o Zeus PC junto com o Windows"; GroupDescription: "Inicialização:"; Flags: unchecked

[Files]
Source: "..\dist\ZeusPC.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ZeusPC"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir o Zeus PC"; Flags: nowait postinstall skipifsilent
