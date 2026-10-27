# W4-02: temporary start of the recorder with Task Scheduler (until W0-07 moves it to a real service, NSSM).
# Runs whether anyone is logged on or not (S4U, no stored password, Limited), at system start and from 06:55
# every minute (a dead recorder comes back within a minute). One instance only (the recorder holds a lock file).
# To stop it for good: Disable-ScheduledTask genesis_w4_recorder, then create var\w4-camera\STOP_RECORDER
# (with the task enabled, STOP_RECORDER only restarts it: the next minute starts a fresh recorder).
#   powershell -ExecutionPolicy Bypass -File register_recorder_task.ps1            register (or update) and start
#   powershell -ExecutionPolicy Bypass -File register_recorder_task.ps1 -Remove    remove the task
param([switch]$Remove, [switch]$NoStart)

$name = 'genesis_w4_recorder'
if ($Remove) {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Output "removed $name"
    return
}

$app = Split-Path -Parent $PSScriptRoot           # apps\w4-camera
$py = 'C:\Program Files\Python312\python.exe'     # stdlib only, nothing to install
$action = New-ScheduledTaskAction -Execute $py -Argument '-m w4cam.recorder' -WorkingDirectory $app
# Daily 06:55, repeated every minute for 24 h: a recorder that was killed or crashed is back within a minute.
# (Measured 2026-10-26: "restart on failure" does NOT restart a process that was killed after it started.)
# While it runs, the repeats are ignored (MultipleInstances IgnoreNew + the recorder's lock file).
$daily = New-ScheduledTaskTrigger -Daily -At '06:55'
$daily.Repetition = (New-ScheduledTaskTrigger -Once -At '06:55' -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 1)).Repetition
$triggers = @((New-ScheduledTaskTrigger -AtStartup), $daily)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Principal $principal -Settings $settings `
    -Description 'Genesis W4-02: camera 102 -> E:\data\video (ffmpeg -c copy), 07:00-22:00. Temporary until W0-07.' `
    -Force | Out-Null
Write-Output "registered $name ($app)"
if (-not $NoStart) {
    Start-ScheduledTask -TaskName $name
    Write-Output "started $name"
}
