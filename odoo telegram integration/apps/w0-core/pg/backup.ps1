# Daily backup of the Genesis database (W0-03): pg_dump -Fc to F:\genesis\backup, keep the newest 14.
# Run by the scheduled task genesis_pg_backup. Passwords come from secrets\pgpass.conf (never printed).
# Exit code 0 = ok, 1 = failed; one JSON line per run in var\logs\w0-pg-backup\backup.jsonl.
$ErrorActionPreference = 'Continue'
$Root  = (Resolve-Path -LiteralPath "$PSScriptRoot\..\..\..").Path
$Bin   = 'C:\Program Files\PostgreSQL\17\bin'
$Dest  = 'F:\genesis\backup'
$Keep  = 14
$Db    = 'genesis'
$LogDir = Join-Path $Root 'var\logs\w0-pg-backup'
$env:PGPASSFILE = Join-Path $Root 'secrets\pgpass.conf'
$env:LC_MESSAGES = 'C'
$stamp = Get-Date -Format 'yyyy-MM-dd_HHmm'
$started = Get-Date

function Write-Log([string]$Level, [string]$Msg, [hashtable]$Extra = @{}) {
    $o = [ordered]@{ ts = (Get-Date).ToUniversalTime().ToString('o'); level = $Level; app = 'w0-pg-backup'; msg = $Msg }
    foreach ($k in $Extra.Keys) { $o[$k] = $Extra[$k] }
    Add-Content -LiteralPath (Join-Path $LogDir 'backup.jsonl') -Value ($o | ConvertTo-Json -Compress) -Encoding utf8
}

New-Item -ItemType Directory -Force -Path "$LogDir", "$Dest" | Out-Null
$dump = Join-Path $Dest "genesis_$stamp.dump"
$part = "$dump.partial"
$errFile = Join-Path $LogDir 'last_stderr.txt'
try {
    & "$Bin\pg_dump.exe" -h 127.0.0.1 -p 5433 -U postgres -w -d "$Db" -Fc -Z 6 -f "$part" 2> "$errFile"
    if ($LASTEXITCODE -ne 0) { throw "pg_dump exit $LASTEXITCODE" }
    & "$Bin\pg_restore.exe" --list "$part" 2>> "$errFile" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "pg_restore --list cannot read the dump" }
    Move-Item -LiteralPath "$part" -Destination "$dump"
    $globals = Join-Path $Dest "globals_$stamp.sql"
    & "$Bin\pg_dumpall.exe" -h 127.0.0.1 -p 5433 -U postgres -w --globals-only --no-role-passwords -f "$globals" 2>> "$errFile"
    if ($LASTEXITCODE -ne 0) { throw "pg_dumpall --globals-only exit $LASTEXITCODE" }

    foreach ($pattern in 'genesis_*.dump', 'globals_*.sql') {
        Get-ChildItem -LiteralPath "$Dest" -Filter $pattern -File | Sort-Object Name -Descending |
            Select-Object -Skip $Keep | Remove-Item -Force
    }
    $size = (Get-Item -LiteralPath "$dump").Length
    Set-Content -LiteralPath (Join-Path $Dest 'LAST_OK.txt') -Value "$stamp $size" -Encoding ascii
    Write-Log 'INFO' 'backup ok' @{ file = $dump; bytes = $size; seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1) }
    exit 0
} catch {
    if (Test-Path -LiteralPath "$part") { Remove-Item -LiteralPath "$part" -Force }
    $err = if (Test-Path -LiteralPath "$errFile") { (Get-Content -LiteralPath "$errFile" -Raw) } else { '' }
    Write-Log 'ERROR' "backup failed: $($_.Exception.Message)" @{ stderr = "$err".Trim() }
    exit 1
}
