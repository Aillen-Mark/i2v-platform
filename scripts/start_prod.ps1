# start_prod.ps1 - production-style launcher (uvicorn 4 workers + docker stack)
# ASCII only on purpose: Windows PowerShell 5.1 reads BOM-less .ps1 as ANSI,
# so non-ASCII text would break parsing.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\start_prod.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\start_prod.ps1 -Workers 2
param(
    [int]$Workers = 4,
    [int]$Port = 8000,
    [int]$HashPoolWorkers = 4,
    # 0 = 沿用 .env 里的 MOCK_SUCCESS_RATE；压测时可传 1.0 让 Mock 稳定出片
    [double]$MockSuccessRate = 0,
    [switch]$SkipDocker
)

$ErrorActionPreference = 'Continue'

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Step([string]$msg) { Write-Host "[start] $msg" }

# ---------------------------------------------------------------- docker ----
function Resolve-Docker {
    $candidates = @(
        'E:\Docker\DockerDesktop\resources\bin\docker.exe',
        'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
    )
    $onPath = Get-Command docker -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

if (-not $SkipDocker) {
    $docker = Resolve-Docker
    if (-not $docker) {
        Write-Warning 'docker CLI not found; skip container startup (use -SkipDocker to silence)'
    } else {
        Write-Step "docker = $docker"
        Push-Location $root
        try {
            # docker 会把进度写到 stderr，PowerShell 会当成错误；这里显式忽略
            $prev = $ErrorActionPreference
            $ErrorActionPreference = 'SilentlyContinue'
            & $docker compose up -d *> $null
            $ErrorActionPreference = $prev
            $deadline = (Get-Date).AddSeconds(120)
            do {
                Start-Sleep -Seconds 3
                $ErrorActionPreference = 'SilentlyContinue'
                $ps = & $docker compose ps --format '{{.Name}}|{{.Status}}' 2>$null
                $ErrorActionPreference = $prev
                $unhealthy = @($ps | Where-Object { $_ -match 'unhealthy|starting|Restarting' })
            } while ($unhealthy.Count -gt 0 -and (Get-Date) -lt $deadline)
            Write-Step 'containers:'
            $ps | ForEach-Object { Write-Host "        $_" }
        } finally {
            Pop-Location
        }
    }
}

# ------------------------------------------------------------ backend ----
$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    $pids = ($existing | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '
    Write-Error "port $Port is already in use by PID(s) $pids. Run scripts\stop_prod.ps1 first."
    exit 1
}
if (-not (Test-Path $python)) {
    Write-Error "python venv not found: $python"
    exit 1
}

Write-Step "starting uvicorn with $Workers workers on 127.0.0.1:$Port"

# Windows PowerShell 5.1 bug: if the environment holds BOTH 'NO_PROXY' and 'no_proxy'
# (same for HTTP_PROXY/HTTPS_PROXY/ALL_PROXY), Start-Process throws
# "Item has already been added. Key in dictionary". Collapse each pair into one uppercase key.
foreach ($base in @('HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'ALL_PROXY')) {
    $value = $null
    foreach ($key in @([System.Environment]::GetEnvironmentVariables().Keys)) {
        if ($key -ieq $base) {
            if (-not $value) { $value = [System.Environment]::GetEnvironmentVariable($key, 'Process') }
            Remove-Item "Env:$key" -ErrorAction SilentlyContinue
        }
    }
    if ($value) { Set-Item -Path "Env:$base" -Value $value }
}

$env:HASH_POOL_WORKERS = "$HashPoolWorkers"
if ($MockSuccessRate -gt 0) {
    $env:MOCK_SUCCESS_RATE = "$MockSuccessRate"
    Write-Step "MOCK_SUCCESS_RATE override = $MockSuccessRate"
}
$env:LOG_LEVEL = 'INFO'
$stdout = Join-Path $logDir 'backend.out.log'
$stderr = Join-Path $logDir 'backend.err.log'
$proc = Start-Process -FilePath $python `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$Port", '--workers', "$Workers" `
    -WorkingDirectory $backend `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru
Write-Step "supervisor PID = $($proc.Id)"

$deadline = (Get-Date).AddSeconds(90)
$healthy = $false
do {
    Start-Sleep -Seconds 2
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/healthz" -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -eq 200) { $healthy = $true; $body = $response.Content }
    } catch {
        $healthy = $false
    }
} while (-not $healthy -and (Get-Date) -lt $deadline)

if (-not $healthy) {
    Write-Error "backend did not become healthy in 90s; check $stderr"
    exit 1
}

Write-Step "healthz -> $body"
$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
Write-Step "listening PIDs on ${Port}: $($listeners -join ', ')"
Write-Host ''
Write-Host '  Candidate  : http://127.0.0.1:8080'
Write-Host '  Admin      : http://127.0.0.1:8080/console/login'
Write-Host '  API docs   : http://127.0.0.1:8000/docs'
Write-Host '  Logs       : logs\backend.out.log / logs\backend.err.log'
Write-Host ''
Write-Host '  Stop with  : powershell -ExecutionPolicy Bypass -File scripts\stop_prod.ps1'
