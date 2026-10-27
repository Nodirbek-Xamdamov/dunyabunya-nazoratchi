# Create the Genesis PostgreSQL cluster (W0-03, D-21): E:\pgdata, port 5433, localhost only.
# Uses the existing PostgreSQL 17 binaries; the old cluster (service postgresql-x64-17, port 5432) is not touched.
# Idempotent: skips initdb when the data dir already holds a cluster. Never prints secrets.
$ErrorActionPreference = 'Stop'
$Root    = (Resolve-Path -LiteralPath "$PSScriptRoot\..\..\..").Path
$Bin     = 'C:\Program Files\PostgreSQL\17\bin'
$Data    = 'E:\pgdata'
$Service = 'genesis-postgresql'
$Py      = 'C:\Program Files\Python312\python.exe'
$Here    = $PSScriptRoot
$env:LC_MESSAGES = 'C'

if (Get-Service -Name "postgresql-x64-17" -ErrorAction SilentlyContinue) { "old cluster service present (left untouched)" }

if (-not (Test-Path -LiteralPath "$Data\PG_VERSION")) {
    $pwfile = Join-Path $Root 'secrets\_initdb_pw.tmp'
    try {
        & "$Py" -E "$Here\pgsecrets.py" --initdb-pwfile "$pwfile"
        if ($LASTEXITCODE -ne 0) { throw "pgsecrets.py failed" }
        & "$Bin\initdb.exe" -D "$Data" -U postgres "--pwfile=$pwfile" -A scram-sha-256 -E UTF8 `
            --locale=C --locale-provider=icu --icu-locale=und --data-checksums
        if ($LASTEXITCODE -ne 0) { throw "initdb failed ($LASTEXITCODE)" }
    } finally {
        if (Test-Path -LiteralPath "$pwfile") { Remove-Item -LiteralPath "$pwfile" -Force }
    }
} else {
    "initdb skipped: $Data already has a cluster"
    & "$Py" -E "$Here\pgsecrets.py"
}

# ACL: only Administrators, SYSTEM and the service account (NetworkService) on the data dir.
# Grant on the folder only, then make every child inherit it (OI/CI flags do not apply to files with /T).
& icacls "$Data" /inheritance:r /grant:r "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*S-1-5-20:(OI)(CI)F" /C /Q
if ($LASTEXITCODE -ne 0) { throw "icacls (folder) failed" }
& icacls "$Data\*" /reset /T /C /Q
if ($LASTEXITCODE -ne 0) { throw "icacls (reset children) failed" }

# Settings: our file is included last, so it wins over the defaults initdb wrote.
Copy-Item -LiteralPath "$Here\genesis.conf" -Destination "$Data\genesis.conf" -Force
Copy-Item -LiteralPath "$Here\pg_hba.conf" -Destination "$Data\pg_hba.conf" -Force
$conf = "$Data\postgresql.conf"
if (-not (Select-String -LiteralPath "$conf" -Pattern "^include 'genesis.conf'" -Quiet)) {
    Add-Content -LiteralPath "$conf" -Value "`ninclude 'genesis.conf'" -Encoding ascii
}

if (-not (Get-Service -Name "$Service" -ErrorAction SilentlyContinue)) {
    & "$Bin\pg_ctl.exe" register -N "$Service" -U "NT AUTHORITY\NetworkService" -D "$Data" -S auto -w
    if ($LASTEXITCODE -ne 0) { throw "pg_ctl register failed" }
}
# SCM restart rules fire only if pg_ctl itself dies; a crashed postmaster is reported as a normal
# stop, which genesis_pg_watchdog (register_watchdog_task.ps1) covers.
& sc.exe failure "$Service" reset= 86400 actions= restart/5000/restart/15000/restart/60000 | Out-Null
& sc.exe failureflag "$Service" 1 | Out-Null
$svc = Get-Service -Name "$Service"
if ($svc.Status -ne 'Running') { Start-Service -Name "$Service" } else { Restart-Service -Name "$Service" }

for ($i = 0; $i -lt 30; $i++) {
    & "$Bin\pg_isready.exe" -h 127.0.0.1 -p 5433 | Out-Null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 1
}
& "$Bin\pg_isready.exe" -h 127.0.0.1 -p 5433
Get-Service -Name "$Service" | Select-Object Name, Status, StartType
