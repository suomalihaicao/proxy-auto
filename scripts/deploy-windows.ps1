param()

$ErrorActionPreference = "Stop"

$BaseDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CurrentDir = Get-Location
Set-Location $BaseDir
$DataDir = Join-Path $BaseDir "data"
$EnvToolsDir = Join-Path $BaseDir "env_tools"
$EnvToolsBin = Join-Path $EnvToolsDir "bin"
$PythonHome = Join-Path $EnvToolsDir "python"
$PythonExe = Join-Path $PythonHome "python.exe"
$SettingsPath = Join-Path $DataDir "settings.json"
$DbPath = Join-Path $DataDir "app.db"
$VenvDir = Join-Path $BaseDir ".venv"
$PipCacheDir = Join-Path $EnvToolsDir "pip-cache"
$MinimumPythonMajor = 3
$MinimumPythonMinor = 10
$PythonVersion = $null
$DeployMode = "start_only"
$NonInteractiveDefault = (($env:PROXY_AUTO_NON_INTERACTIVE -eq "1") -and $env:PROXY_AUTO_NON_INTERACTIVE -ne $null)

New-Item -ItemType Directory -Force -Path $DataDir, $EnvToolsDir, $EnvToolsBin, $PipCacheDir | Out-Null
if ($PSVersionTable.PSVersion.Major -lt 5) {
  throw "PowerShell 5.0+ is required. Please use PowerShell 5.1 or higher."
}

Write-Host ("[deploy] Windows minimum environment: PowerShell {0}+ / Python {1}.{2}+ / Invoke-RestMethod + Invoke-WebRequest" -f 5, $MinimumPythonMajor, $MinimumPythonMinor)
Write-Host ("[deploy] Environment tools directory: {0} (dependency cache and installer files will be placed here)" -f $EnvToolsDir)

function Write-DeployLog {
  param([string]$Message)
  Write-Host "[deploy] $Message"
}

function Show-Usage {
  Write-Host "[deploy] usage: .\deploy-windows.ps1 [--start-only|--interactive]"
  Write-Host "[deploy]  --start-only     non-interactive mode (default), only start service, configure proxy settings in web panel"
  Write-Host "[deploy]  --interactive    interactive mode, write settings and admin parameters manually"
}

foreach ($arg in $args) {
  switch ($arg) {
    "--start-only" { $DeployMode = "start_only" ; continue }
    "--start" { $DeployMode = "start_only" ; continue }
    "--no-interactive" { $DeployMode = "start_only" ; continue }
    "--interactive" { $DeployMode = "interactive"; continue }
    "--help" { Show-Usage; exit 0 }
    { $_ -like "-h" } { Show-Usage; exit 0 }
    default {
      Write-DeployLog ("Unsupported argument: {0}" -f $arg)
      Show-Usage
      exit 1
    }
  }
}

if ($NonInteractiveDefault -and $DeployMode -ne "interactive") {
  $DeployMode = "start_only"
}

if ($DeployMode -eq "start_only") {
  Write-DeployLog "Non-interactive mode: skip proxy parameter prompts in terminal; configure in web panel after service starts."
}

function Parse-IntValue {
  param(
    [string]$Value,
    [int]$Default,
    [int]$Min = [int]::MinValue,
    [int]$Max = [int]::MaxValue
  )
  if ([string]::IsNullOrWhiteSpace($Value)) {
    return $Default
  }
  try {
    $parsed = [int]$Value
  } catch {
    return $Default
  }
  if ($parsed -lt $Min) { return $Min }
  if ($parsed -gt $Max) { return $Max }
  return $parsed
}

function New-SessionSecret {
  param([string]$PythonBinary)
  if (Get-Command openssl -ErrorAction SilentlyContinue) {
    try {
      return (& openssl rand -hex 24).Trim()
    } catch {
      # fall through
    }
  }
  return (& $PythonBinary -c "import secrets; print(secrets.token_hex(24))").Trim()
}

function Write-DeploymentSettings {
  param(
    [string]$ListenHost,
    [string]$ListenPort,
    [string]$WebPort,
    [string]$ProxyMode,
    [string]$ProxyProtocol,
    [string]$ProxyHost,
    [string]$ProxyPort,
    [string]$ProxyUser,
    [string]$ProxyPass,
    [string]$ApiUrl,
    [string]$BigdataApiUrl,
    [string]$BigdataApiToken,
    [string]$SessionSecret
  )

  $settings = [ordered]@{
    listen_host = $ListenHost
    listen_port = Parse-IntValue -Value $ListenPort -Default 3128 -Min 1 -Max 65535
    web_host = "0.0.0.0"
    web_port = Parse-IntValue -Value $WebPort -Default 6666 -Min 1 -Max 65535
    proxy_mode = $ProxyMode
    proxy_protocol = $ProxyProtocol
    proxy_host = $ProxyHost
    proxy_port = Parse-IntValue -Value $ProxyPort -Default 0 -Min 0 -Max 65535
    proxy_username = $ProxyUser
    proxy_password = $ProxyPass
    api_url = $ApiUrl
    api_method = "GET"
    api_timeout = 8
    api_cache_ttl = 20
    api_headers = ""
    api_body = ""
    api_host_key = "host"
    api_port_key = "port"
    api_username_key = "username"
    api_password_key = "password"
    api_proxy_field = "proxy"
    bigdata_api_url = $BigdataApiUrl
    bigdata_api_token = $BigdataApiToken
    allowed_client_ips = ""
    session_secret = $SessionSecret
  }

  $settingsJson = $settings | ConvertTo-Json -Depth 2
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($SettingsPath, $settingsJson, $utf8NoBom)
}

function Ensure-DefaultSettings {
  param([string]$PythonBinary)
  if (Test-Path -Path $SettingsPath) {
    return
  }

  Write-DeployLog "settings.json not found, create default config (direct mode). Configure web/proxy settings in dashboard."
  Write-DeploymentSettings `
    -ListenHost "0.0.0.0" `
    -ListenPort "3128" `
    -WebPort "6666" `
    -ProxyMode "direct" `
    -ProxyProtocol "http" `
    -ProxyHost "" `
    -ProxyPort "0" `
    -ProxyUser "" `
    -ProxyPass "" `
    -ApiUrl "" `
    -BigdataApiUrl "" `
    -BigdataApiToken "" `
    -SessionSecret (New-SessionSecret -PythonBinary $PythonBinary)
}

function Read-WebPortFromSettings {
  param([string]$Fallback = "6666")
  if (-not (Test-Path -Path $SettingsPath)) {
    return Parse-IntValue -Value $Fallback -Default 6666 -Min 1 -Max 65535
  }
  try {
    $cfg = Get-Content -Raw -Path $SettingsPath | ConvertFrom-Json
    if ($null -ne $cfg.web_port) {
      return Parse-IntValue -Value ([string]$cfg.web_port) -Default 6666 -Min 1 -Max 65535
    }
  } catch {
    return Parse-IntValue -Value $Fallback -Default 6666 -Min 1 -Max 65535
  }
  return Parse-IntValue -Value $Fallback -Default 6666 -Min 1 -Max 65535
}

function Ensure-Admin {
  param([string]$PythonBinary, [string]$AdminUser, [string]$AdminPassword)
  Write-DeployLog "Initialize DB and ensure admin user"
  $env:DB_PATH = $DbPath
  $env:PROXY_ADMIN_USER = $AdminUser
  $env:PROXY_ADMIN_PASSWORD = $AdminPassword

  $adminBootstrapPath = Join-Path ([System.IO.Path]::GetTempPath()) "proxy_auto_ensure_admin.py"
  $adminBootstrap = @'
import os
from pathlib import Path
from app.db import init_db, get_user, create_user

db_path = Path(os.environ["DB_PATH"])
admin_user = os.environ["PROXY_ADMIN_USER"]
admin_password = os.environ["PROXY_ADMIN_PASSWORD"]

init_db(db_path)
if get_user(db_path, admin_user) is None:
    create_user(db_path, admin_user, admin_password)
    print("Admin created: {}".format(admin_user))
else:
    print("Admin exists: {}".format(admin_user))
'@

  $prevPythonPath = $env:PYTHONPATH
  $prevLocation = Get-Location
  $env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($prevPythonPath)) {
    $BaseDir
  } else {
    "$BaseDir;$prevPythonPath"
  }

  Set-Location $BaseDir
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($adminBootstrapPath, $adminBootstrap, $utf8NoBom)
  try {
    & $PythonBinary $adminBootstrapPath
  } finally {
    Remove-Item -Path $adminBootstrapPath -ErrorAction SilentlyContinue
    if ($prevLocation) {
      Set-Location $prevLocation.Path
    }
    if ([string]::IsNullOrWhiteSpace($prevPythonPath)) {
      Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
      $env:PYTHONPATH = $prevPythonPath
    }
  }
}

function Get-PanelHostHint {
  param([string]$PublicIp)
  $overrideHost = $env:PROXY_AUTO_PANEL_HOST
  if (-not [string]::IsNullOrWhiteSpace($overrideHost)) {
    return $overrideHost.Trim()
  }

  if (Test-Path -Path $SettingsPath) {
    try {
      $cfg = Get-Content -Raw -Path $SettingsPath | ConvertFrom-Json
      if ($null -ne $cfg.web_host) {
        $webHost = [string]$cfg.web_host
        if (-not [string]::IsNullOrWhiteSpace($webHost)) {
          $webHost = $webHost.Trim()
          if ($webHost -ne "0.0.0.0" -and $webHost -ne "::") {
            return $webHost
          }
        }
      }
    } catch {
      # ignore invalid settings file
    }
  }

  if ([string]::IsNullOrWhiteSpace($PublicIp) -or $PublicIp -eq "Public IP not detected") {
    return "127.0.0.1"
  }
  return $PublicIp
}

function Start-ServiceInTerminal {
  param(
    [string]$PythonBinary,
    [string]$WebPort
  )

  $serviceArgs = "-m uvicorn app.main:app --host 0.0.0.0 --port $WebPort"
  $serviceCommand = "`"$PythonBinary`" $serviceArgs"

  if (Get-Command powershell.exe -ErrorAction SilentlyContinue) {
    return Start-Process -FilePath "powershell.exe" -ArgumentList @(
      "-NoProfile",
      "-NoExit",
      "-ExecutionPolicy",
      "Bypass",
      "-Command",
      "Set-Location `"$BaseDir`"; $serviceCommand"
    ) -PassThru
  }

  if (Get-Command cmd.exe -ErrorAction SilentlyContinue) {
    return Start-Process -FilePath "cmd.exe" -ArgumentList @(
      "/k",
      "cd /d `"$BaseDir`" && $serviceCommand"
    ) -PassThru
  }

  throw "No terminal executable found, cannot start service window"
}

function Get-PidsByPort {
  param([int]$PortToCheck)

  $procIds = New-Object System.Collections.ArrayList

  try {
    $listeners = Get-NetTCPConnection -State Listen -LocalPort $PortToCheck -ErrorAction Stop
    foreach ($listener in $listeners) {
      if ($null -ne $listener.OwningProcess) {
        [void]$procIds.Add([int]$listener.OwningProcess)
      }
    }
  } catch {
    $netstatLines = netstat -ano -p tcp 2>$null
    foreach ($line in $netstatLines) {
      if ($line -match "LISTENING") {
        $parts = ($line -replace '^\s+', '') -split '\s+'
        if ($parts.Count -ge 5) {
          $localAddress = [string]$parts[1]
          $state = [string]$parts[3]
          $procIdText = [string]$parts[4]
          if ($state -eq "LISTENING" -and $localAddress.EndsWith(":$PortToCheck")) {
            if (-not [string]::IsNullOrWhiteSpace($procIdText)) {
              [void]$procIds.Add([int]$procIdText)
            }
          }
        }
      }
    }
  }

  return $procIds | Sort-Object -Unique
}

function Ensure-WebPortFree {
  param([int]$Port)

  if ($Port -le 0 -or $Port -gt 65535) {
    throw "Invalid port: $Port"
  }

  $procIds = @(Get-PidsByPort -PortToCheck $Port)
  if (-not $procIds -or $procIds.Count -eq 0) {
    Write-DeployLog "Port ${Port} is free"
    return
  }

  Write-DeployLog "Port ${Port} is occupied, stopping processes: $($procIds -join ',')"
  foreach ($procId in $procIds) {
    try {
      $proc = Get-Process -Id $procId -ErrorAction Stop
      if ($null -ne $proc) {
        Write-DeployLog "Stopping process: $($proc.ProcessName) (PID=$procId)"
        try {
          Stop-Process -Id $procId -Force -ErrorAction Stop
        } catch {
          & taskkill.exe /F /PID $procId 2>$null | Out-Null
        }
      }
    } catch {
      # Process already exited.
    }
  }

  $retries = 6
  for ($i = 0; $i -lt $retries; $i++) {
    Start-Sleep -Seconds 1
    $remaining = @(Get-PidsByPort -PortToCheck $Port)
    if (-not $remaining -or $remaining.Count -eq 0) {
      Write-DeployLog "Port ${Port} released"
      return
    }
  }

  throw "Port ${Port} is still occupied: $($procIds -join ',')"
}

function Get-WebLoginUrl {
  param([string]$HostHint, [int]$Port)
  return "http://${HostHint}:$Port/login"
}

function Wait-ForServiceReady {
  param(
    [string]$HostHint,
    [string]$Port,
    [int]$Attempts = 20,
    [int]$DelaySeconds = 1
  )

  $healthUrl = "http://{0}:{1}/health" -f $HostHint, $Port
  for ($i = 0; $i -lt $Attempts; $i++) {
    try {
      $resp = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
      if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) {
        return $true
      }
    } catch {
      Start-Sleep -Seconds $DelaySeconds
    }
  }
  return $false
}

function Open-WebPanel {
  param(
    [string]$HostHint,
    [string]$Port,
    [string]$AdminUser,
    [string]$AdminPassword
  )

  $webUrl = Get-WebLoginUrl -HostHint $HostHint -Port $Port
  Write-DeployLog ("Web URL: {0}" -f $webUrl)
  Write-DeployLog ("Default login: {0} / {1}" -f $AdminUser, $AdminPassword)

  try {
    Start-Process $webUrl
    Write-DeployLog "Browser opened automatically"
  } catch {
    Write-DeployLog "Failed to open browser automatically, please visit: $webUrl"
  }
}

function Get-LocalPythonVersion {
  param(
    [string]$Executable,
    [string[]]$BaseArgs = @()
  )
  try {
    $raw = & $Executable @BaseArgs -V 2>&1
    if ($LASTEXITCODE -ne 0) {
      return $null
    }
    if ($raw -match 'Python\s+(\d+)\.(\d+)') {
      return [version]("$($matches[1]).$($matches[2]).0")
    }
    return $null
  } catch {
    return $null
  }
}

function Test-PythonVersion {
  param([Version]$Ver)
  if ($null -eq $Ver) {
    return $false
  }
  return ($Ver.Major -gt $MinimumPythonMajor) -or ($Ver.Major -eq $MinimumPythonMajor -and $Ver.Minor -ge $MinimumPythonMinor)
}

function Install-PythonWithManager {
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    Write-DeployLog "Trying to install Python via winget..."
    try {
      & winget install --id Python.Python.3 --accept-package-agreements --accept-source-agreements --silent --disable-interactivity --force | Out-Null
      return $true
    } catch {
      Write-DeployLog "winget installation failed, try next method"
    }
  }

  if (Get-Command choco -ErrorAction SilentlyContinue) {
    Write-DeployLog "Trying to install Python via chocolatey..."
    try {
      & choco install -y python | Out-Null
      return $true
    } catch {
      Write-DeployLog "choco installation failed, try next method"
    }
  }

  return $false
}

function Get-PythonInstallerLatency {
  param([string]$Url)
  try {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $resp = Invoke-WebRequest -Uri $Url -Method Head -UseBasicParsing -TimeoutSec 6 -MaximumRedirection 5 -ErrorAction Stop
    $sw.Stop()
    if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) {
      return [double]$sw.Elapsed.TotalSeconds
    }
  } catch {
    try {
      $sw = [System.Diagnostics.Stopwatch]::StartNew()
      $resp = Invoke-WebRequest -Uri $Url -Method Get -UseBasicParsing -TimeoutSec 6 -MaximumRedirection 5 -ErrorAction Stop
      $sw.Stop()
      if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) {
        return [double]$sw.Elapsed.TotalSeconds
      }
    } catch {
      return $null
    }
  }
  return $null
}

function Select-FastestPythonInstaller {
  param(
    [string]$ArchitectureTag,
    [string]$InstallerVersion
  )

  $installerFile = "python-$InstallerVersion-$ArchitectureTag.exe"
  $candidates = @(
    @{ Name = "Python official"; Url = "https://www.python.org/ftp/python/$InstallerVersion/$installerFile" },
    @{ Name = "Aliyun mirror"; Url = "https://mirrors.aliyun.com/python/$InstallerVersion/$installerFile" },
    @{ Name = "Huawei mirror"; Url = "https://mirrors.huaweicloud.com/python/$InstallerVersion/$installerFile" }
  )

  $best = [double]::PositiveInfinity
  $selected = $candidates[0].Url
  $hasAvailable = $false

  foreach ($candidate in $candidates) {
    $latency = Get-PythonInstallerLatency -Url $candidate.Url
    if ($null -ne $latency) {
      Write-DeployLog ("Python installer available: {0} | latency={1}s" -f $candidate.Name, [math]::Round($latency, 3))
      if ($latency -lt $best) {
        $best = $latency
        $selected = $candidate.Url
      }
      $hasAvailable = $true
    } else {
      Write-DeployLog ("Python installer unreachable: {0} ({1})" -f $candidate.Name, $candidate.Url)
    }
  }

  if (-not $hasAvailable) {
    Write-DeployLog "Python installer probe failed, fallback to official source"
    return "https://www.python.org/ftp/python/$InstallerVersion/$installerFile"
  }

  Write-DeployLog ("Selected fastest Python installer: {0}" -f $selected)
  return $selected
}

function Install-PythonToEnvTools {
  $archTag = ""
  switch ($env:PROCESSOR_ARCHITECTURE) {
    "AMD64" { $archTag = "amd64"; break }
    "ARM64" { $archTag = "arm64"; break }
    "x86" { $archTag = "win32"; break }
    default { return $false }
  }

  $installerVersion = "3.11.9"
  $installerFile = "python-$installerVersion-$archTag.exe"
  $installerUrl = Select-FastestPythonInstaller -ArchitectureTag $archTag -InstallerVersion $installerVersion
  $installerPath = Join-Path $EnvToolsDir $installerFile

  Write-DeployLog "Trying to download Python installer to project dir: $installerUrl"
  try {
    Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing -MaximumRedirection 10 | Out-Null
  } catch {
    Write-DeployLog "Python installer download failed: $($_.Exception.Message)"
    return $false
  }

  try {
    $args = @(
      "/quiet",
      "InstallAllUsers=0",
      "PrependPath=0",
      "Include_pip=1",
      "TargetDir=$PythonHome"
    )
    Start-Process -Wait -FilePath $installerPath -ArgumentList $args | Out-Null
    Remove-Item -Path $installerPath -Force -ErrorAction SilentlyContinue
    return (Test-Path $PythonExe)
  } catch {
    Write-DeployLog "Python installer execution failed: $($_.Exception.Message)"
    return $false
  }
}

function Ensure-PythonRuntime {
  $version = Get-LocalPythonVersion -Executable $PythonExe
  if (Test-PythonVersion -Ver $version) {
    Write-DeployLog "Using project Python: $PythonExe"
    $global:PythonVersion = $version
    return $PythonExe
  }

  if (Get-Command py -ErrorAction SilentlyContinue) {
    $launcherVer = Get-LocalPythonVersion -Executable "py" -BaseArgs @("-3")
    if (Test-PythonVersion -Ver $launcherVer) {
      $global:PythonVersion = $launcherVer
      Write-DeployLog "Using py launcher"
      return "py|-3"
    }
  }

  if (Get-Command python3 -ErrorAction SilentlyContinue) {
    $cmd = (Get-Command python3).Source
    $version = Get-LocalPythonVersion -Executable $cmd
    if (Test-PythonVersion -Ver $version) {
      $global:PythonVersion = $version
      Write-DeployLog "Using system python3: $cmd"
      return $cmd
    }
  }

  if (Get-Command python -ErrorAction SilentlyContinue) {
    $cmd = (Get-Command python).Source
    $version = Get-LocalPythonVersion -Executable $cmd
    if (Test-PythonVersion -Ver $version) {
      $global:PythonVersion = $version
      Write-DeployLog "Using system python: $cmd"
      return $cmd
    }
  }

  if (Install-PythonWithManager) {
    if (Get-Command python3 -ErrorAction SilentlyContinue) {
      $cmd = (Get-Command python3).Source
      $version = Get-LocalPythonVersion -Executable $cmd
      if (Test-PythonVersion -Ver $version) {
        $global:PythonVersion = $version
        Write-DeployLog "Use system python3 after package-manager install: $cmd"
        return $cmd
      }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
      $cmd = (Get-Command python).Source
      $version = Get-LocalPythonVersion -Executable $cmd
      if (Test-PythonVersion -Ver $version) {
        $global:PythonVersion = $version
        Write-DeployLog "Use system python after package-manager install: $cmd"
        return $cmd
      }
    }
  }

  if (Install-PythonToEnvTools) {
    $version = Get-LocalPythonVersion -Executable $PythonExe
    if (Test-PythonVersion -Ver $version) {
      $global:PythonVersion = $version
      Write-DeployLog "Installed Python in project directory: $PythonExe"
      return $PythonExe
    }
  }

  return $null
}

function Get-LocalIps {
  try {
    (Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred -ErrorAction Stop |
      Where-Object { $_.IPAddress -notlike "127.*" } |
      Select-Object -ExpandProperty IPAddress) -join ", "
  } catch {
    "IPv4 not detected"
  }
}

function Get-PublicIp {
  $endpoints = @(
    "https://api.ipify.org"
    "https://ifconfig.me/ip"
    "https://icanhazip.com"
  )
  foreach ($target in $endpoints) {
    try {
      $resp = Invoke-RestMethod -Uri $target -TimeoutSec 4 -ErrorAction Stop
      if ($resp) {
        return ($resp -replace '\s','')
      }
    } catch {
      continue
    }
  }
  "Public IP not detected"
}

function Get-PipMirrorLatency {
  param([string]$Url)
  try {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $resp = Invoke-WebRequest -Uri "$Url/" -Method Head -UseBasicParsing -TimeoutSec 6 -ErrorAction Stop
    $sw.Stop()
    if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) {
      return $sw.Elapsed.TotalSeconds
    }
  } catch {
    try {
      $sw = [System.Diagnostics.Stopwatch]::StartNew()
      $resp = Invoke-WebRequest -Uri "$Url/" -Method Get -UseBasicParsing -TimeoutSec 6 -ErrorAction Stop
      $sw.Stop()
      if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400) {
        return $sw.Elapsed.TotalSeconds
      }
    } catch {
      return $null
    }
    return $null
  }
}

function Select-FastestPipSource {
  $mirrors = @(
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.org/simple"
  )

  $best = [double]::PositiveInfinity
  $picked = $mirrors[1]
  $hasAvailable = $false

  foreach ($mirror in $mirrors) {
    $latency = Get-PipMirrorLatency -Url $mirror
    if ($null -ne $latency) {
      Write-DeployLog "Mirror available: $mirror | latency=$([math]::Round($latency,3))s"
      if ($latency -lt $best) {
        $best = $latency
        $picked = $mirror
      }
      $hasAvailable = $true
    } else {
      Write-DeployLog "Mirror unreachable: $mirror"
    }
  }

  if (-not $hasAvailable) {
    Write-DeployLog "Mirror probe failed, fallback to https://pypi.org/simple"
    return "https://pypi.org/simple"
  }

  Write-DeployLog "Selected fastest pip source: $picked"
  return $picked
}

function Invoke-Python {
  param(
    [string[]]$Arguments,
    [string]$Executable,
    [string[]]$BaseArgs = @()
  )
  if ($BaseArgs.Count -gt 0) {
    & $Executable @BaseArgs @Arguments
  } else {
    & $Executable @Arguments
  }
}

function Ensure-VenvPip {
  param(
    [string]$VenvPython,
    [string]$VenvPip,
    [string]$EnvToolsDir
  )

  if (Test-Path -Path $VenvPip) {
    return
  }

  Write-DeployLog "Venv missing pip, trying ensurepip recovery..."
  try {
    & $VenvPython -m ensurepip --upgrade | Out-Null
    if (Test-Path -Path $VenvPip) {
      return
    }
  } catch {
    Write-DeployLog "ensurepip recovery failed, continue with get-pip.py..."
  }

  $getPipScript = Join-Path $EnvToolsDir "get-pip.py"
  try {
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPipScript -UseBasicParsing -MaximumRedirection 10 | Out-Null
  } catch {
    throw "Cannot download get-pip.py, pip install cannot proceed"
  }

  try {
    & $VenvPython $getPipScript --no-warn-script-location | Out-Null
  } catch {
    throw "get-pip.py execution failed, unable to install pip in venv"
  }

  if (-not (Test-Path -Path $VenvPip)) {
    throw "Venv pip installation failed"
  }
}

$pythonInfo = Ensure-PythonRuntime
if ([string]::IsNullOrWhiteSpace($pythonInfo)) {
  throw "No suitable Python 3.10+, automation install not completed. Check network/permissions and retry."
}

if ($pythonInfo -eq "py|-3") {
  $pythonExe = "py"
  $pythonArgs = @("-3")
} else {
  $pythonExe = $pythonInfo
  $pythonArgs = @()
}
Write-DeployLog ("Using Python: {0} {1}" -f $pythonExe, ($pythonArgs -join " "))

function Invoke-BootstrapPython {
  param([string[]]$Arguments)
  Invoke-Python -Executable $pythonExe -BaseArgs $pythonArgs -Arguments $Arguments
}

Write-DeployLog "Start deployment (project: $BaseDir)"
Write-DeployLog "Intranet IP: $(Get-LocalIps)"
Write-DeployLog "Public IP: $(Get-PublicIp)"

if (-not (Test-Path -Path $VenvDir)) {
  Write-DeployLog "No existing venv found, creating: $VenvDir ..."
  Invoke-BootstrapPython @("-m","venv",$VenvDir)
} else {
  $existingVenvPython = Join-Path $VenvDir "Scripts\python.exe"
  try {
    & $existingVenvPython -V | Out-Null
  } catch {
    Write-DeployLog "Existing venv is broken, recreating: $VenvDir ..."
    Remove-Item -Path $VenvDir -Recurse -Force
    Invoke-BootstrapPython @("-m","venv",$VenvDir)
  }
}

$PipExe = Join-Path $VenvDir "Scripts\pip.exe"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $PipExe)) {
  Ensure-VenvPip -VenvPython $VenvPython -VenvPip $PipExe -EnvToolsDir $EnvToolsDir
}

$Env:PIP_CACHE_DIR = $PipCacheDir
$PipIndex = Select-FastestPipSource

Write-DeployLog "Installing dependencies into project venv"
& $PipExe install --disable-pip-version-check --no-input --upgrade pip -i $PipIndex
& $PipExe install --disable-pip-version-check --no-input -r (Join-Path $BaseDir "requirements.txt") -i $PipIndex --cache-dir $PipCacheDir

$proxyMode = "direct"
$protocol = "http"
$proxyHost = ""
$proxyPort = "0"
$proxyUser = ""
$proxyPass = ""
$apiUrl = ""
$bigdataApiUrl = ""
$bigdataApiToken = ""
$listenHost = "0.0.0.0"
$listenPort = "3128"
$webPort = Read-WebPortFromSettings -Fallback "6666"
$adminUser = "admin"
$adminPassword = "admin123"
$sessionSecret = New-SessionSecret -PythonBinary $VenvPython

if ($DeployMode -eq "interactive") {
  $inputMode = Read-Host "Upstream proxy mode [single_ip/api/bigdata_api/direct] (default single_ip)"
  if ([string]::IsNullOrWhiteSpace($inputMode)) { $inputMode = "single_ip" }
  $proxyMode = $inputMode.Trim().ToLower()
  switch ($proxyMode) {
    "http" { $protocol = "http"; $proxyMode = "single_ip" }
    "socks5" { $protocol = "socks5"; $proxyMode = "single_ip" }
    "single_ip" {}
    "api" {}
    "bigdata_api" {}
    "direct" {}
    default {
      throw "Unsupported proxy mode: $proxyMode"
    }
  }

  if ($proxyMode -eq "single_ip") {
    $inputProtocol = Read-Host "Single IP protocol [http/socks5] (default http)"
    if (-not [string]::IsNullOrWhiteSpace($inputProtocol)) {
      $protocol = $inputProtocol.ToLower()
    }
    if ($protocol -ne "http" -and $protocol -ne "socks5") {
      throw "Unsupported protocol: $protocol"
    }
    $proxyHost = Read-Host "Single IP host (e.g. 127.0.0.1)"
    $proxyPort = Read-Host "Single IP port (e.g. 6666)"
    $proxyUser = Read-Host "Single IP username (optional)"
    $proxyPass = Read-Host "Single IP password (optional)"
  }
  elseif ($proxyMode -eq "api") {
    $apiUrl = Read-Host "API URL (required)"
    if ([string]::IsNullOrWhiteSpace($apiUrl)) {
      throw "API mode requires api_url"
    }
  }
  elseif ($proxyMode -eq "bigdata_api") {
    $bigdataApiUrl = Read-Host "BigData API URL"
    $apiUrl = Read-Host "API URL (optional, fallback)"
    $bigdataApiToken = Read-Host "BigData token (optional)"
    if ([string]::IsNullOrWhiteSpace($bigdataApiUrl) -and [string]::IsNullOrWhiteSpace($apiUrl)) {
      throw "BigData mode requires at least one API URL"
    }
  }

  $listenHostInput = Read-Host "Listen host for proxy [0.0.0.0]"
  if (-not [string]::IsNullOrWhiteSpace($listenHostInput)) { $listenHost = $listenHostInput.Trim() }
  $listenPortInput = Read-Host "Listen port for proxy [3128]"
  if (-not [string]::IsNullOrWhiteSpace($listenPortInput)) { $listenPort = $listenPortInput.Trim() }
  $webPortInput = Read-Host "Web listen port [6666]"
  if (-not [string]::IsNullOrWhiteSpace($webPortInput)) { $webPort = $webPortInput.Trim() }

  $adminUserInput = Read-Host "Admin user [admin]"
  if (-not [string]::IsNullOrWhiteSpace($adminUserInput)) { $adminUser = $adminUserInput.Trim() }
  $adminPasswordInput = Read-Host "Admin password [admin123]"
  if (-not [string]::IsNullOrWhiteSpace($adminPasswordInput)) { $adminPassword = $adminPasswordInput }

  Write-DeploymentSettings -ListenHost $listenHost -ListenPort $listenPort -WebPort $webPort -ProxyMode $proxyMode -ProxyProtocol $protocol -ProxyHost $proxyHost -ProxyPort $proxyPort -ProxyUser $proxyUser -ProxyPass $proxyPass -ApiUrl $apiUrl -BigdataApiUrl $bigdataApiUrl -BigdataApiToken $bigdataApiToken -SessionSecret $sessionSecret
} else {
  Ensure-DefaultSettings -PythonBinary $VenvPython
}

Ensure-Admin -PythonBinary $VenvPython -AdminUser $adminUser -AdminPassword $adminPassword

if ($DeployMode -eq "interactive") {
  $startNow = Read-Host "Start service now? [Y/n]"
} else {
  $startNow = "Y"
}

$webPort = Parse-IntValue -Value ([string]$webPort) -Default 6666 -Min 1 -Max 65535

if ($startNow -match '^[Nn]$') {
  Write-DeployLog "Skip auto-start. Manual command: .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port $webPort"
} else {
  $publicIp = Get-PublicIp
  $webHostHint = Get-PanelHostHint -PublicIp $publicIp
  Ensure-WebPortFree -Port ([int]$webPort)
  $proc = Start-ServiceInTerminal -PythonBinary $VenvPython -WebPort $webPort
  Write-DeployLog "Service terminal started, PID=$($proc.Id)"

  if (Wait-ForServiceReady -HostHint "127.0.0.1" -Port $webPort -Attempts 20 -DelaySeconds 1) {
    Open-WebPanel -HostHint $webHostHint -Port $webPort -AdminUser $adminUser -AdminPassword $adminPassword
  } else {
    Write-DeployLog "Health check timeout (about 20s), please verify the service status manually."
    Write-DeployLog "Suggested URL: $(Get-WebLoginUrl -HostHint $webHostHint -Port $webPort)"
  }
}

Write-DeployLog "Deployment completed"
if ($startNow -match '^[Nn]$') {
  $publicIp = Get-PublicIp
  $webHostHint = Get-PanelHostHint -PublicIp $publicIp
  Write-DeployLog "Web URL: $(Get-WebLoginUrl -HostHint $webHostHint -Port $webPort)"
  Write-DeployLog ("Default login: {0} / {1}" -f $adminUser, $adminPassword)
}
