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

New-Item -ItemType Directory -Force -Path $DataDir, $EnvToolsDir, $EnvToolsBin, $PipCacheDir | Out-Null
if ($PSVersionTable.PSVersion.Major -lt 5) {
  throw "PowerShell 5.0+ 是最低要求。请使用 PowerShell 5.1 或更高版本。"
}

Write-Host ("[deploy] Windows 最低环境: PowerShell {0}+ / Python {1}.{2}+ / Invoke-RestMethod + Invoke-WebRequest" -f 5, $MinimumPythonMajor, $MinimumPythonMinor)
Write-Host ("[deploy] 项目工具目录: {0}（依赖缓存与安装器会放在该目录）" -f $EnvToolsDir)

function Write-DeployLog {
  param([string]$Message)
  Write-Host "[deploy] $Message"
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
    Write-DeployLog "尝试通过 winget 安装 Python..."
    try {
      & winget install --id Python.Python.3 --accept-package-agreements --accept-source-agreements --silent --disable-interactivity --force | Out-Null
      return $true
    } catch {
      Write-DeployLog "winget 安装失败，尝试下一种方式"
    }
  }

  if (Get-Command choco -ErrorAction SilentlyContinue) {
    Write-DeployLog "尝试通过 chocolatey 安装 Python..."
    try {
      & choco install -y python | Out-Null
      return $true
    } catch {
      Write-DeployLog "choco 安装失败，尝试下一种方式"
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
    @{ Name = "Python 官方"; Url = "https://www.python.org/ftp/python/$InstallerVersion/$installerFile" },
    @{ Name = "阿里源"; Url = "https://mirrors.aliyun.com/python/$InstallerVersion/$installerFile" },
    @{ Name = "华为源"; Url = "https://mirrors.huaweicloud.com/python/$InstallerVersion/$installerFile" }
  )

  $best = [double]::PositiveInfinity
  $selected = $candidates[0].Url
  $hasAvailable = $false

  foreach ($candidate in $candidates) {
    $latency = Get-PythonInstallerLatency -Url $candidate.Url
    if ($null -ne $latency) {
      Write-DeployLog ("Python 安装包可用: {0} | 延迟={1}s" -f $candidate.Name, [math]::Round($latency, 3))
      if ($latency -lt $best) {
        $best = $latency
        $selected = $candidate.Url
      }
      $hasAvailable = $true
    } else {
      Write-DeployLog ("Python 安装包不可达: {0} ({1})" -f $candidate.Name, $candidate.Url)
    }
  }

  if (-not $hasAvailable) {
    Write-DeployLog "Python 安装包源探测失败，回退官方源"
    return "https://www.python.org/ftp/python/$InstallerVersion/$installerFile"
  }

  Write-DeployLog ("已选择最快 Python 安装源: {0}" -f $selected)
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

  Write-DeployLog "尝试下载 Python 安装包到项目目录：$installerUrl"
  try {
    Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing -MaximumRedirection 10 | Out-Null
  } catch {
    Write-DeployLog "Python 安装包下载失败：$($_.Exception.Message)"
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
    Write-DeployLog "官方安装包执行失败：$($_.Exception.Message)"
    return $false
  }
}

function Ensure-PythonRuntime {
  $version = Get-LocalPythonVersion -Executable $PythonExe
  if (Test-PythonVersion -Ver $version) {
    Write-DeployLog "使用项目目录 Python: $PythonExe"
    $global:PythonVersion = $version
    return $PythonExe
  }

  if (Get-Command py -ErrorAction SilentlyContinue) {
    $launcherVer = Get-LocalPythonVersion -Executable "py" -BaseArgs @("-3")
    if (Test-PythonVersion -Ver $launcherVer) {
      $global:PythonVersion = $launcherVer
      Write-DeployLog "使用 py launcher"
      return "py|-3"
    }
  }

  if (Get-Command python3 -ErrorAction SilentlyContinue) {
    $cmd = (Get-Command python3).Source
    $version = Get-LocalPythonVersion -Executable $cmd
    if (Test-PythonVersion -Ver $version) {
      $global:PythonVersion = $version
      Write-DeployLog "使用系统 python3: $cmd"
      return $cmd
    }
  }

  if (Get-Command python -ErrorAction SilentlyContinue) {
    $cmd = (Get-Command python).Source
    $version = Get-LocalPythonVersion -Executable $cmd
    if (Test-PythonVersion -Ver $version) {
      $global:PythonVersion = $version
      Write-DeployLog "使用系统 python: $cmd"
      return $cmd
    }
  }

  if (Install-PythonWithManager) {
    if (Get-Command python3 -ErrorAction SilentlyContinue) {
      $cmd = (Get-Command python3).Source
      $version = Get-LocalPythonVersion -Executable $cmd
      if (Test-PythonVersion -Ver $version) {
        $global:PythonVersion = $version
        Write-DeployLog "通过包管理器安装后使用系统 python3: $cmd"
        return $cmd
      }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
      $cmd = (Get-Command python).Source
      $version = Get-LocalPythonVersion -Executable $cmd
      if (Test-PythonVersion -Ver $version) {
        $global:PythonVersion = $version
        Write-DeployLog "通过包管理器安装后使用系统 python: $cmd"
        return $cmd
      }
    }
  }

  if (Install-PythonToEnvTools) {
    $version = Get-LocalPythonVersion -Executable $PythonExe
    if (Test-PythonVersion -Ver $version) {
      $global:PythonVersion = $version
      Write-DeployLog "已在项目目录安装 Python: $PythonExe"
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
    "未检测到 IPv4"
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
  "未检测到公网 IP"
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
      Write-DeployLog "镜像可用: $mirror | 延迟=$([math]::Round($latency,3))s"
      if ($latency -lt $best) {
        $best = $latency
        $picked = $mirror
      }
      $hasAvailable = $true
    } else {
      Write-DeployLog "镜像不可达: $mirror"
    }
  }

  if (-not $hasAvailable) {
    Write-DeployLog "镜像探测失败，回退官方源 https://pypi.org/simple"
    return "https://pypi.org/simple"
  }

  Write-DeployLog "已选择最快 pip 源: $picked"
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

  Write-DeployLog "虚拟环境缺少 pip，尝试通过 ensurepip 修复..."
  try {
    & $VenvPython -m ensurepip --upgrade | Out-Null
    if (Test-Path -Path $VenvPip) {
      return
    }
  } catch {
    Write-DeployLog "ensurepip 修复失败，继续尝试官方脚本..."
  }

  $getPipScript = Join-Path $EnvToolsDir "get-pip.py"
  try {
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPipScript -UseBasicParsing -MaximumRedirection 10 | Out-Null
  } catch {
    throw "无法下载 get-pip.py，无法完成 pip 安装"
  }

  try {
    & $VenvPython $getPipScript --no-warn-script-location | Out-Null
  } catch {
    throw "get-pip.py 执行失败，无法在虚拟环境中安装 pip"
  }

  if (-not (Test-Path -Path $VenvPip)) {
    throw "虚拟环境 pip 安装失败"
  }
}

$pythonInfo = Ensure-PythonRuntime
if ([string]::IsNullOrWhiteSpace($pythonInfo)) {
  throw "未检测到 Python3.10+，自动化安装未完成，请确认网络与权限后重试。"
}

if ($pythonInfo -eq "py|-3") {
  $pythonExe = "py"
  $pythonArgs = @("-3")
} else {
  $pythonExe = $pythonInfo
  $pythonArgs = @()
}
Write-DeployLog ("使用 Python: {0} {1}" -f $pythonExe, ($pythonArgs -join " "))

function Invoke-BootstrapPython {
  param([string[]]$Arguments)
  Invoke-Python -Executable $pythonExe -BaseArgs $pythonArgs -Arguments $Arguments
}

Write-DeployLog "开始部署（项目：$BaseDir）"
Write-DeployLog "内网 IP: $(Get-LocalIps)"
Write-DeployLog "公网 IP: $(Get-PublicIp)"

if (-not (Test-Path -Path $VenvDir)) {
  Write-DeployLog "未检测到现有虚拟环境，正在创建 $VenvDir ..."
  Invoke-BootstrapPython @("-m","venv",$VenvDir)
} else {
  $existingVenvPython = Join-Path $VenvDir "Scripts\python.exe"
  try {
    & $existingVenvPython -V | Out-Null
  } catch {
    Write-DeployLog "现有虚拟环境异常，准备重建 $VenvDir ..."
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

Write-DeployLog "安装依赖到项目虚拟环境"
& $PipExe install --disable-pip-version-check --no-input --upgrade pip -i $PipIndex
& $PipExe install --disable-pip-version-check --no-input -r (Join-Path $BaseDir "requirements.txt") -i $PipIndex --cache-dir $PipCacheDir

$proxyMode = Read-Host "上游代理模式 [single_ip/api/bigdata_api/direct] (回车默认 single_ip)"
if ([string]::IsNullOrWhiteSpace($proxyMode)) { $proxyMode = "single_ip" }
$proxyMode = $proxyMode.Trim().ToLower()
$protocol = "http"
$proxyHost = ""
$proxyPort = "0"
$proxyUser = ""
$proxyPass = ""
$apiUrl = ""
$bigdataApiUrl = ""
$bigdataApiToken = ""

switch ($proxyMode) {
  "http" { $protocol = "http"; $proxyMode = "single_ip" }
  "socks5" { $protocol = "socks5"; $proxyMode = "single_ip" }
  "single_ip" {}
  "api" {}
  "bigdata_api" {}
  "direct" {}
  default {
    throw "不支持的模式: $proxyMode"
  }
}

if ($proxyMode -eq "single_ip") {
  $inputProtocol = Read-Host "单 IP 代理协议 [http/socks5] (回车默认 http)"
  if (-not [string]::IsNullOrWhiteSpace($inputProtocol)) {
    $protocol = $inputProtocol.ToLower()
  }
  if ($protocol -ne "http" -and $protocol -ne "socks5") {
    throw "不支持的协议: $protocol"
  }
  $proxyHost = Read-Host "单 IP 代理地址 (例如: 127.0.0.1)"
  $proxyPort = Read-Host "单 IP 代理端口 (例如: 8080)"
  $proxyUser = Read-Host "单 IP 账号（可空）"
  $proxyPass = Read-Host "单 IP 密码（可空）"
}
elseif ($proxyMode -eq "api") {
  $apiUrl = Read-Host "API 地址（必填）"
  if ([string]::IsNullOrWhiteSpace($apiUrl)) {
    throw "API 模式需要填写 api_url"
  }
}
elseif ($proxyMode -eq "bigdata_api") {
  $bigdataApiUrl = Read-Host "BigData API 地址"
  $apiUrl = Read-Host "API 地址（可空，作为 fallback）"
  $bigdataApiToken = Read-Host "BigData Token（可空）"
  if ([string]::IsNullOrWhiteSpace($bigdataApiUrl) -and [string]::IsNullOrWhiteSpace($apiUrl)) {
    throw "BigData 模式需要至少填写一个 API 地址"
  }
}

$listenHost = Read-Host "代理监听地址 [0.0.0.0]"
if ([string]::IsNullOrWhiteSpace($listenHost)) { $listenHost = "0.0.0.0" }
$listenPort = Read-Host "代理监听端口 [3128]"
if ([string]::IsNullOrWhiteSpace($listenPort)) { $listenPort = "3128" }
$webPort = Read-Host "Web 监听端口 [8080]"
if ([string]::IsNullOrWhiteSpace($webPort)) { $webPort = "8080" }

$adminUser = Read-Host "管理员用户名 [admin]"
if ([string]::IsNullOrWhiteSpace($adminUser)) { $adminUser = "admin" }
$adminPassword = Read-Host "管理员密码 [admin123]"
if ([string]::IsNullOrWhiteSpace($adminPassword)) { $adminPassword = "admin123" }

if (Get-Command openssl -ErrorAction SilentlyContinue) {
  $sessionSecret = (& openssl rand -hex 24).Trim()
} else {
  $sessionSecret = (& $VenvPython -c "import secrets; print(secrets.token_hex(24))").Trim()
}

$settings = [ordered]@{
  listen_host = $listenHost
  listen_port = [int]$listenPort
  web_host = "0.0.0.0"
  web_port = [int]$webPort
  proxy_mode = $proxyMode
  proxy_protocol = $protocol
  proxy_host = $proxyHost
  proxy_port = [int]$proxyPort
  proxy_username = $proxyUser
  proxy_password = $proxyPass
  api_url = $apiUrl
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
  bigdata_api_url = $bigdataApiUrl
  bigdata_api_token = $bigdataApiToken
  allowed_client_ips = ""
  session_secret = $sessionSecret
}

$settingsJson = $settings | ConvertTo-Json -Depth 2
Set-Content -Path $SettingsPath -Value $settingsJson -Encoding UTF8

Write-DeployLog "初始化数据库并创建管理员账号"
$env:DB_PATH = $DbPath
$env:PROXY_ADMIN_USER = $adminUser
$env:PROXY_ADMIN_PASSWORD = $adminPassword
& $VenvPython -c @'
import os
from app.db import init_db, get_user, create_user

db_path = os.environ["DB_PATH"]
admin_user = os.environ["PROXY_ADMIN_USER"]
admin_password = os.environ["PROXY_ADMIN_PASSWORD"]

init_db(db_path)
if get_user(db_path, admin_user) is None:
    create_user(db_path, admin_user, admin_password)
    print(f"已创建管理员: {admin_user}")
else:
    print(f"管理员已存在: {admin_user}")
'@

$startNow = Read-Host "部署完成后立即启动服务？[Y/n]"
if ($startNow -match '^[Nn]$') {
  Write-DeployLog "已跳过启动，后续启动命令: .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port $webPort"
} else {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $VenvPython
  $psi.Arguments = "-m uvicorn app.main:app --host 0.0.0.0 --port $webPort"
  $psi.WorkingDirectory = $BaseDir
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $false
  $proc = [System.Diagnostics.Process]::Start($psi)
  Write-DeployLog "服务已启动，PID=$($proc.Id)"
}

Write-DeployLog "部署完成"
Write-DeployLog ("Web 地址: http://{0}:{1}/login" -f (Get-PublicIp), $webPort)
Write-DeployLog ("默认登录: {0} / {1}" -f $adminUser, $adminPassword)
