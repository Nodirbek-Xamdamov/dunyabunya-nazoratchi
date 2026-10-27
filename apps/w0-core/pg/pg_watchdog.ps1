# Watchdog for genesis-postgresql (run every minute by the task genesis_pg_watchdog).
# pg_ctl reports a crashed postmaster to Windows as a normal stop, so SCM recovery actions never fire;
# this script starts the service again. Skipped while var\run\pg_maintenance.flag exists (planned work).
# Touches only genesis-postgresql, never the old postgresql-x64-17.
$ErrorActionPreference = 'Continue'
$Root    = (Resolve-Path -LiteralPath "$PSScriptRoot\..\..\..").Path
$Service = 'genesis-postgresql'
$Flag    = Join-Path $Root 'var\run\pg_maintenance.flag'
$LogDir  = Join-Path $Root 'var\logs\w0-pg-watchdog'

function Write-Log([string]$Level, [string]$Msg) {
    New-Item -ItemType Directory -Force -Path "$LogDir" | Out-Null
    $o = [ordered]@{ ts = (Get-Date).ToUniversalTime().ToString('o'); level = $Level; app = 'w0-pg-watchdog'; msg = $Msg }
    Add-Content -LiteralPath (Join-Path $LogDir 'watchdog.jsonl') -Value ($o | ConvertTo-Json -Compress) -Encoding utf8
}

if (Test-Path -LiteralPath "$Flag") { exit 0 }
$svc = Get-Service -Name "$Service" -ErrorAction SilentlyContinue
if (-not $svc) { Write-Log 'ERROR' "service $Service not found"; exit 1 }
if ($svc.StartType -ne 'Automatic' -or $svc.Status -ne 'Stopped') { exit 0 }

Write-Log 'WARN' "service $Service was stopped; starting it"
try {
    Start-Service -Name "$Service" -ErrorAction Stop
    $svc.WaitForStatus('Running', [TimeSpan]::FromSeconds(60))
    Write-Log 'INFO' "service $Service running again"
    exit 0
} catch {
    Write-Log 'ERROR' "start failed: $($_.Exception.Message)"
    exit 1
}
