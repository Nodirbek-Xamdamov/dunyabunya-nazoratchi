# P1-1: bot_watchdog.ps1'ni Windows Task Scheduler'ga ro'yxatga oladi - bot 24/7 ishlashi uchun.
# Vazifa har daqiqada va server yonganda ishga tushadi: /health javob bermasa botni yoqadi.
# Bir marta ishga tushiriladi (yoki serverni ko'chirganda qayta).
#
# Ishlatish: powershell -ExecutionPolicy Bypass -File setup_watchdog_task.ps1

param(
    [string]$TaskName = "dunyabunya_bot_watchdog"
)

$ErrorActionPreference = "Stop"
$ScriptPath = Join-Path $PSScriptRoot "bot_watchdog.ps1"
$ProjectDir = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path $ScriptPath)) {
    throw "bot_watchdog.ps1 topilmadi: $ScriptPath"
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -File `"$ScriptPath`"" -WorkingDirectory $ProjectDir
$everyMinute = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$atBoot = New-ScheduledTaskTrigger -AtStartup
# IgnoreNew: oldingi tekshiruv hali tugamagan bo'lsa yangisi boshlanmaydi (ikkita bot yonib ketmasin)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
# S4U: vazifa foydalanuvchi tizimga KIRMAGAN bo'lsa ham ishlaydi (parol saqlanmaydi). Sukutdagi
# "Interactive" turida server qayta yongach kimdir RDP orqali kirmaguncha bot yonmasdi.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $everyMinute, $atBoot -Settings $settings -Principal $principal `
    -Description "dunyabunya CEO agent: bot ishlab turganini har daqiqada tekshiradi, o'chgan bo'lsa yoqadi (P1-1)" `
    -Force | Out-Null

Write-Output "Vazifa ro'yxatga olindi: $TaskName - har daqiqada va server yonganda $ScriptPath ishga tushadi."
Write-Output "Tekshirish: Get-ScheduledTaskInfo -TaskName $TaskName"
Write-Output "Botni qayta ishga tushirish: powershell -ExecutionPolicy Bypass -File scripts\restart_bot.ps1"
Write-Output "O'chirish: Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
