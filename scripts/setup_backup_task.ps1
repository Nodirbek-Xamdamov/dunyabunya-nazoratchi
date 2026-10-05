# P0-4: backup_db.ps1'ni Windows Task Scheduler'ga kunlik vazifa sifatida
# ro'yxatga oladi (Linux'dagi cron'ning ekvivalenti). Bir marta ishga
# tushiriladi (yoki serverni ko'chirganda qayta).
#
# Ishlatish: powershell -ExecutionPolicy Bypass -File setup_backup_task.ps1

$ErrorActionPreference = "Stop"
$ScriptPath = Join-Path $PSScriptRoot "backup_db.ps1"

if (-not (Test-Path $ScriptPath)) {
    throw "backup_db.ps1 topilmadi: $ScriptPath"
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -File `"$ScriptPath`""
$trigger = New-ScheduledTaskTrigger -Daily -At 3:00AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd
# S4U: vazifa foydalanuvchi tizimga KIRMAGAN bo'lsa ham ishlaydi (parol saqlanmaydi). Sukutdagi
# "Interactive" turida server qayta yongach kimdir RDP orqali kirmaguncha backup olinmasdi.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName "dunyabunya_db_backup" `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "dunyabunya CEO agent: kunlik PostgreSQL yordamchi baza zaxira nusxasi (P0-4)" `
    -Force

Write-Output "Vazifa ro'yxatga olindi: har kuni 03:00 da $ScriptPath ishga tushadi."
Write-Output "Tekshirish: Get-ScheduledTaskInfo -TaskName dunyabunya_db_backup"
Write-Output "Qo'lda sinash: Start-ScheduledTask -TaskName dunyabunya_db_backup"
Write-Output "O'chirish: Unregister-ScheduledTask -TaskName dunyabunya_db_backup -Confirm:`$false"
