; Instalador do JFD – Intelligent Audio Studio (Inno Setup 6.3 ou mais novo)
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{B7E2A6C4-3F1D-4A59-8E0B-6D2C9A41F0E7}
AppName=JFD - Intelligent Audio Studio
AppVersion={#AppVersion}
AppVerName=JFD - Intelligent Audio Studio {#AppVersion}
AppPublisher=JFD
DefaultDirName={autopf}\JFD
DefaultGroupName=JFD
DisableProgramGroupPage=yes
LicenseFile=..\LICENCA.txt
OutputDir=..\dist
OutputBaseFilename=JFD_Setup
SetupIconFile=..\jfd\assets\jfd.ico
UninstallDisplayIcon={app}\JFD.exe
UninstallDisplayName=JFD - Intelligent Audio Studio
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ExtraDiskSpaceRequired=1073741824

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos:"

[Files]
Source: "..\dist\JFD\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\LICENCA.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\AVISOS-DE-TERCEIROS.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\JFD"; Filename: "{app}\JFD.exe"; Comment: "Seu estúdio musical inteligente"
Name: "{autodesktop}\JFD"; Filename: "{app}\JFD.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\JFD.exe"; Description: "Abrir o JFD"; Flags: nowait postinstall skipifsilent

[Code]
type
  TMemStatusEx = record
    dwLength: Cardinal;
    dwMemoryLoad: Cardinal;
    ullTotalPhys: Int64;
    ullAvailPhys: Int64;
    ullTotalPageFile: Int64;
    ullAvailPageFile: Int64;
    ullTotalVirtual: Int64;
    ullAvailVirtual: Int64;
    ullAvailExtendedVirtual: Int64;
  end;

function GlobalMemoryStatusEx(var lpBuffer: TMemStatusEx): Boolean;
  external 'GlobalMemoryStatusEx@kernel32.dll stdcall';

function InitializeSetup(): Boolean;
var
  Mem: TMemStatusEx;
  TotalGB: Int64;
begin
  Result := True;
  if not IsWin64 then
  begin
    MsgBox('O JFD precisa do Windows 10 ou 11 de 64 bits.', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  Mem.dwLength := SizeOf(Mem);
  if GlobalMemoryStatusEx(Mem) then
  begin
    TotalGB := Mem.ullTotalPhys div (1024 * 1024 * 1024);
    if TotalGB < 7 then
      MsgBox('Este computador tem menos de 8 GB de memória. O JFD funciona, mas a separação de ' +
             'instrumentos será lenta e deve ser usada no perfil Leve.', mbInformation, MB_OK);
  end;
end;
