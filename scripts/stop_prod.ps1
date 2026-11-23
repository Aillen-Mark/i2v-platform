# stop_prod.ps1 - stop ONLY the i2v backend (uvicorn app.main:app) by port / command line.
# Never touches other python processes on the machine (e.g. research scripts).
# ASCII only: Windows PowerShell 5.1 reads BOM-less .ps1 as ANSI.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\stop_prod.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\stop_prod.ps1 -Port 8000 -KeepDocker
param(
    [int]$Port = 8000,
    [switch]$KeepDocker
)

$ErrorActionPreference = 'Continue'

$root = Split-Path -Parent $PSScriptRoot

function Get-I2vProcesses {
    <# uvicorn supervisor + its worker children, matched by command line (not by name) #>
    $all = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'"
    $matched = @()
    foreach ($proc in $all) {
        $cmd = [string]$proc.CommandLine
        if ($cmd -match 'uvicorn' -and $cmd -match 'app\.main:app') {
            $matched += $proc
        }
    }
    # include descendants (multiprocessing spawn workers of --workers N)
    $ids = @($matched | Select-Object -ExpandProperty ProcessId)
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($proc in $all) {
            if ($ids -contains $proc.ParentProcessId -and -not ($ids -contains $proc.ProcessId)) {
                $ids += $proc.ProcessId
                $changed = $true
            }
        }
    }
    return $ids
}

$targets = @()

# 1) processes owning the listening port
$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listeners) {
    $targets += ($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
}

# 2) uvicorn processes matched by command line (covers supervisor without listener)
$targets += (Get-I2vProcesses)

$targets = @($targets | Where-Object { $_ -and $_ -ne $PID } | Select-Object -Unique)

if (-not $targets) {
    Write-Host "[stop] no i2v backend process found on port $Port"
} else {
    Write-Host "[stop] stopping PID(s): $($targets -join ', ')"
    foreach ($id in $targets) {
        $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host ("        PID {0,-7} {1,-22} started {2}" -f $proc.Id, $proc.ProcessName, $proc.StartTime)
            Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Seconds 2
}

$still = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($still) {
    Write-Warning "port $Port is still listening by PID(s) $(($still | Select-Object -ExpandProperty OwningProcess -Unique) -join ', ')"
    exit 1
}
Write-Host "[stop] port $Port is free"

if (-not $KeepDocker) {
    $dockerCandidates = @(
        'E:\Docker\DockerDesktop\resources\bin\docker.exe',
        'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
    )
    $docker = (Get-Command docker -ErrorAction SilentlyContinue).Source
    if (-not $docker) {
        foreach ($candidate in $dockerCandidates) { if (Test-Path $candidate) { $docker = $candidate; break } }
    }
    if ($docker) {
        Push-Location $root
        try {
            Write-Host '[stop] docker compose stop (data volumes are kept)'
            & $docker compose stop 2>&1 | Out-Null
        } finally {
            Pop-Location
        }
    } else {
        Write-Warning 'docker CLI not found; containers left running'
    }
} else {
    Write-Host '[stop] -KeepDocker: containers left running'
}

Write-Host '[stop] done'
