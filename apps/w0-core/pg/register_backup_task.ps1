# Register the scheduled task genesis_pg_backup: daily 02:30, runs backup.ps1 without a logged-on user
# (S4U, limited rights, like the other watchdog tasks on this server). Re-running updates the task.
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'backup.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At '02:30'
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName 'genesis_pg_backup' -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description 'Genesis W0-03: daily pg_dump of genesis (port 5433) to F:\genesis\backup' -Force |
    Select-Object TaskName, State
