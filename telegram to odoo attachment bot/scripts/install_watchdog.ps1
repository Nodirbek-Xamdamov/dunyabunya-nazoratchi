# Watchdog'ni Windows Task Scheduler'ga o'rnatadi: har 1 daqiqada scripts\watchdog.ps1.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_watchdog.ps1            # joriy foydalanuvchi, tizimga kirgan bo'lsa
#   powershell -ExecutionPolicy Bypass -File scripts\install_watchdog.ps1 -S4U       # kirmagan bo'lsa ham (restartdan keyin ham)
#
# -S4U: "foydalanuvchi tizimga kirmagan bo'lsa ham ishlasin", parol saqlanmaydi. Bot faqat internetga
# (Telegram, Odoo, OpenAI) chiqadi, tarmoq papkalari kerak emas, shuning uchun S4U yetarli.
# O'chirish: Unregister-ScheduledTask -TaskName telegram_odoo_bot_watchdog -Confirm:$false

param([switch]$S4U)

$ErrorActionPreference = "Stop"
$TaskName = "telegram_odoo_bot_watchdog"
$Script = Join-Path $PSScriptRoot "watchdog.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`""
$every = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$triggers = @($every)
$logon = if ($S4U) { "S4U" } else { "Interactive" }
if ($S4U) { $triggers += New-ScheduledTaskTrigger -AtStartup }
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType $logon -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Principal $principal -Settings $settings `
    -Description "telegram-odoo-bot: bot ishlab turganini tekshiradi va kerak bo'lsa qayta ishga tushiradi" -Force | Out-Null
Write-Output "O'rnatildi: $TaskName ($logon, har 1 daqiqada)"
