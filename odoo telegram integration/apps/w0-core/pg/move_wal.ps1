# Move pg_wal of the Genesis cluster to D:\pgwal and leave a junction in E:\pgdata (D-22).
# Stops ONLY the service genesis-postgresql; the old cluster (postgresql-x64-17, port 5432) is not touched.
# The original folder is kept as E:\pgdata\pg_wal.moved until the new location is verified.
$ErrorActionPreference = 'Stop'
$Bin     = 'C:\Program Files\PostgreSQL\17\bin'
$Data    = 'E:\pgdata'
$Wal     = 'D:\pgwal'
$Service = 'genesis-postgresql'
$Sids    = '*S-1-5-32-544:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F', '*S-1-5-20:(OI)(CI)F'   # Administrators, SYSTEM, NetworkService

$item = Get-Item -LiteralPath "$Data\pg_wal" -Force
if ($item.LinkType -eq 'Junction') { "pg_wal is already a junction -> $($item.Target)"; exit 0 }
if ((Test-Path -LiteralPath "$Wal") -and (Get-ChildItem -LiteralPath "$Wal" -Force)) { throw "$Wal exists and is not empty" }

# Planned stop: tell genesis_pg_watchdog not to start the service meanwhile (flag removed at the end).
$Flag = (Resolve-Path -LiteralPath "$PSScriptRoot\..\..\..").Path + '\var\run\pg_maintenance.flag'
New-Item -ItemType Directory -Force -Path (Split-Path $Flag) | Out-Null
Set-Content -LiteralPath "$Flag" -Value "move_wal.ps1 $(Get-Date -Format s)" -Encoding ascii

Stop-Service -Name "$Service"
(Get-Service -Name "$Service").WaitForStatus('Stopped', [TimeSpan]::FromSeconds(60))
if (Test-Path -LiteralPath "$Data\postmaster.pid") { throw "postmaster.pid still present: server not stopped cleanly" }

New-Item -ItemType Directory -Force -Path "$Wal" | Out-Null
& icacls "$Wal" /inheritance:r /grant:r @Sids /C /Q
if ($LASTEXITCODE -ne 0) { throw "icacls $Wal failed" }

& robocopy "$Data\pg_wal" "$Wal" /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NP /NJH /NJS
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }
& icacls "$Wal\*" /reset /T /C /Q
if ($LASTEXITCODE -ne 0) { throw "icacls reset children failed" }

# Verify: same relative names, sizes and hashes.
$src = Get-ChildItem -LiteralPath "$Data\pg_wal" -Recurse -File -Force
foreach ($f in $src) {
    $rel = $f.FullName.Substring("$Data\pg_wal".Length)
    $dst = Join-Path $Wal $rel
    if (-not (Test-Path -LiteralPath "$dst")) { throw "missing after copy: $rel" }
    if ((Get-FileHash -LiteralPath $f.FullName).Hash -ne (Get-FileHash -LiteralPath "$dst").Hash) { throw "hash differs: $rel" }
}
"copied and verified: $($src.Count) files"

Move-Item -LiteralPath "$Data\pg_wal" -Destination "$Data\pg_wal.moved"
New-Item -ItemType Junction -Path "$Data\pg_wal" -Target "$Wal" | Out-Null

Start-Service -Name "$Service"
for ($i = 0; $i -lt 30; $i++) {
    & "$Bin\pg_isready.exe" -h 127.0.0.1 -p 5433 | Out-Null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 1
}
& "$Bin\pg_isready.exe" -h 127.0.0.1 -p 5433
$j = Get-Item -LiteralPath "$Data\pg_wal" -Force
"pg_wal: LinkType=$($j.LinkType) Target=$($j.Target)"
Remove-Item -LiteralPath "$Flag" -Force
