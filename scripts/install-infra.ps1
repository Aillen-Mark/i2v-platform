# Elevated install script: WSL2 runtime + Docker Desktop, all install/data dirs on E:
# Launched by the main session via Start-Process -Verb RunAs. Log: logs\install-infra.log
# NOTE: ASCII only. Windows PowerShell 5.1 reads BOM-less .ps1 as ANSI, which corrupts UTF-8 text.

$ErrorActionPreference = 'Continue'
$logDir = 'E:\DeepSeekHarness\frame\i2v-platform\i2v-platform\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'install-infra.log'

function Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $log -Value $line -Encoding utf8
    Write-Output $line
}

function LogBlock([string]$title, [string]$text) {
    Log "===== $title ====="
    Add-Content -Path $log -Value $text -Encoding utf8
    Write-Output $text
}

Log "start; user=$(whoami)"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Log "isAdmin=$isAdmin"
if (-not $isAdmin) { Log "no admin, abort"; exit 1 }

# Step 1: WSL2 runtime (Store package Microsoft.WSL)
Log "step1: winget install Microsoft.WSL"
$wslOut = & winget install --id Microsoft.WSL -e --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1 | Out-String
LogBlock "winget Microsoft.WSL" $wslOut
Log "winget exit=$LASTEXITCODE"

Start-Sleep -Seconds 2
$wslVer = & wsl --version 2>&1 | Out-String
LogBlock "wsl --version" $wslVer

# Step 2: Docker Desktop with custom dirs
$installer = 'E:\Downloads\DockerDesktopInstaller.exe'
Log "step2: install Docker Desktop -> E:\Docker\DockerDesktop ; data root -> E:\Docker\wsl"
New-Item -ItemType Directory -Force -Path 'E:\Docker\DockerDesktop', 'E:\Docker\wsl', 'E:\Docker\hyperv' | Out-Null

$installerArgs = @(
    'install',
    '--quiet',
    '--accept-license',
    '--backend=wsl-2',
    '--installation-dir=E:\Docker\DockerDesktop',
    '--wsl-default-data-root=E:\Docker\wsl',
    '--hyper-v-default-data-root=E:\Docker\hyperv',
    '--always-run-service'
)
Log ("cmd: " + $installer + " " + ($installerArgs -join ' '))
$proc = Start-Process -FilePath $installer -ArgumentList $installerArgs -Wait -PassThru -NoNewWindow
Log "Docker Desktop installer exit=$($proc.ExitCode)"

# Step 3: ensure current user is in docker-users
$grp = & net localgroup docker-users $env:USERNAME /add 2>&1 | Out-String
LogBlock "docker-users add $env:USERNAME" $grp

# Step 4: verify
Log "step4: verify"
Log ("Docker Desktop.exe exists: " + (Test-Path 'E:\Docker\DockerDesktop\Docker Desktop.exe'))
$ver = & 'E:\Docker\DockerDesktop\resources\docker.exe' --version 2>&1 | Out-String
LogBlock "docker.exe --version" $ver
$wslList = & wsl -l -v 2>&1 | Out-String
LogBlock "wsl -l -v" $wslList

Log "done"
Set-Content -Path (Join-Path $logDir 'install-infra.done') -Value (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') -Encoding utf8
