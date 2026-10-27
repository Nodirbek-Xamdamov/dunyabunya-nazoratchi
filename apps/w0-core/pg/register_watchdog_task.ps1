# Register genesis_pg_watchdog: every minute and at boot runs pg_watchdog.ps1.
# Starting a service needs an elevated token, hence RunLevel Highest (S4U: no stored password).
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'pg_watchdog.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$every = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 1)
$boot = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'genesis_pg_watchdog' -Action $action -Trigger @($every, $boot) -Principal $principal `
    -Settings $settings -Description 'Genesis W0-03: restart genesis-postgresql if it stopped (pg_ctl hides crashes from SCM)' -Force |
    Select-Object TaskName, State
