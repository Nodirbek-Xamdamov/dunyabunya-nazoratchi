# P1-1: Bot ishlab turganini tekshiradi, ishlamasa qayta ishga tushiradi.
#
# Windows Task Scheduler'ning o'zining "vazifa muvaffaqiyatsiz tugasa qayta
# ishga tushirish" (RestartCount/RestartInterval) sozlamasi UZOQ ishlaydigan
# (bloklovchi) vazifalar uchun ishonchsiz ekani sinovda aniqlandi — jarayon
# o'ldirilganda avtomatik tiklanmadi. Shuning uchun buning o'rniga soddaroq,
# ishonchliroq naqsh: bu skript har 1 daqiqada alohida vazifa sifatida
# ishga tushadi (Task Scheduler'ning "takrorlash" trigeri — bu funksiya
# ancha ishonchli), /health'ni tekshiradi, javob bo'lmasa botni ishga
# tushiradi. Bot allaqachon ishlab tursa — hech narsa qilmaydi.

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$OutLog = Join-Path $ProjectDir "run.out.log"
$ErrLog = Join-Path $ProjectDir "run.err.log"
$HealthUrl = "http://127.0.0.1:8002/health"

try {
    $resp = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec 5 -UseBasicParsing
    if ($resp.StatusCode -eq 200) {
        exit 0  # Bot ishlab turibdi — hech narsa qilinmaydi
    }
} catch {
    # So'rov muvaffaqiyatsiz — bot ishlamayapti, pastda qayta ishga tushiramiz
}

# Eski/osilib qolgan jarayon bo'lsa tozalaymiz (masalan port band, lekin
# /health javob bermayapti)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*app.main*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

Start-Process -FilePath $Python -ArgumentList "-m", "app.main" -WorkingDirectory $ProjectDir `
    -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -WindowStyle Hidden

Write-Output "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'): bot ishlamayotgani aniqlandi, qayta ishga tushirildi"
