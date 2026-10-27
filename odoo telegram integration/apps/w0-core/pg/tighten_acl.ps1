# Tighten ACLs (W0-04 residue, SECURITY.md): only SYSTEM, Administrators and the admin user account that
# runs our S4U tasks (genesis_pg_backup, genesis_w4_recorder) keep access; Users / Authenticated Users go.
# The old ACLs are saved first:  -Restore puts them back.
param([switch]$Restore)
$ErrorActionPreference = 'Stop'
$Root = 'D:\Demos\odoo telegram integration'
$Save = Join-Path $Root 'var\acl_backup'
$Targets = [ordered]@{
    secrets = Join-Path $Root 'secrets'
    backup  = 'F:\genesis\backup'
    w0pg    = Join-Path $Root 'apps\w0-core\pg'
}
$UserSid = ([System.Security.Principal.NTAccount]"$env:USERDOMAIN\$env:USERNAME").Translate([System.Security.Principal.SecurityIdentifier]).Value
New-Item -ItemType Directory -Force -Path "$Save" | Out-Null

foreach ($name in $Targets.Keys) {
    $dir = $Targets[$name]
    $file = Join-Path $Save "$name.acl"
    if ($Restore) {
        & icacls (Split-Path $dir) /restore "$file" /C /Q
        if ($LASTEXITCODE -ne 0) { throw "restore $name failed" }
        "restored $dir"
        continue
    }
    if (-not (Test-Path -LiteralPath "$file")) {
        & icacls "$dir" /save "$file" /T /C /Q
        if ($LASTEXITCODE -ne 0) { throw "save $name failed" }
    }
    & icacls "$dir" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*${UserSid}:(OI)(CI)M" /C /Q
    if ($LASTEXITCODE -ne 0) { throw "icacls $name failed" }
    & icacls "$dir\*" /reset /T /C /Q
    if ($LASTEXITCODE -ne 0) { throw "reset children of $name failed" }
    "tightened $dir"
}
