# Botni qayta ishga tushiradi (kod yoki .env o'zgargandan keyin).
#
# 8002-portni tinglayotgan jarayonni va uni ishga tushirgan .venv python'ini to'xtatadi, so'ng
# watchdog vazifasini chaqiradi va /health javob berguncha kutadi. Jarayon buyruq qatori bo'yicha
# emas, PORT egasi bo'yicha topiladi - shu serverdagi boshqa botlarga tegib ketmaslik uchun.
#
# Ishlatish: powershell -ExecutionPolicy Bypass -File scripts\restart_bot.ps1
#
# Jarayon majburan to'xtatiladi: shu paytda bajarilayotgan so'rov javobsiz qoladi, shuning uchun
# hech kim botga yozmayotgan paytda ishga tushiring.

$ErrorActionPreference = "Stop"
$Port = 8002
$HealthUrl = "http://127.0.0.1:$Port/health"
$TaskName = "dunyabunya_bot_watchdog"
$WaitSeconds = 60

$owner = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1).OwningProcess
if ($owner) {
    $ids = @($owner)
    # .venv\Scripts\python.exe asosiy Python'ni bola jarayon qilib ishga tushiradi - ikkalasi to'xtatiladi
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$owner"
    if ($proc) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.ParentProcessId)"
        if ($parent -and $parent.Name -eq "python.exe" -and $parent.CommandLine -match '-m\s+app\.main\s*$') {
            $ids += $parent.ProcessId
        }
    }
    $ids | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
    Write-Output "To'xtatildi: PID $($ids -join ', ')"
    Start-Sleep -Seconds 2
} else {
    Write-Output "Bot ishlamayotgan ekan - ishga tushirilmoqda"
}

Start-ScheduledTask -TaskName $TaskName

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec 5 -UseBasicParsing
        if ($resp.StatusCode -eq 200) {
            Write-Output "Bot ishlayapti: $($resp.Content)"
            exit 0
        }
    } catch {
        # hali ishga tushmagan - kutishda davom etamiz
    }
}

Write-Output "XATO: bot $WaitSeconds soniyada javob bermadi - logs\bot.log va run.err.log ni tekshiring"
exit 1
