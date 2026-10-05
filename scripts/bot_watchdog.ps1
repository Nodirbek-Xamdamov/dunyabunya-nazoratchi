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
    # /health 200 qaytarsa — jarayon ham, Telegram'dan xabar olish (polling) ham ishlayapti.
    # Polling to'xtab/osilib qolgan bo'lsa bot 503 qaytaradi (Invoke-WebRequest xato tashlaydi)
    # va pastda qayta ishga tushiriladi.
    $resp = Invoke-WebRequest -Uri $HealthUrl -TimeoutSec 5 -UseBasicParsing
    if ($resp.StatusCode -eq 200) {
        exit 0  # Bot ishlab turibdi — hech narsa qilinmaydi
    }
} catch {
    # So'rov muvaffaqiyatsiz — bot ishlamayapti, pastda qayta ishga tushiramiz
}

# Eski/osilib qolgan jarayon bo'lsa tozalaymiz (masalan port band, lekin
# /health javob bermayapti)
# DIQQAT: faqat "-m app.main" bilan TUGAYDIGAN buyruq qatori — "*app.main*" ishlatilmaydi,
# chunki u db_web_chatbot'ning "uvicorn app.main:app" jarayoniga ham mos kelib, uni ham
# o'ldirib qo'yardi (2026-09-25 da aniqlandi).
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match '-m\s+app\.main\s*$' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

# Start-Process log faylini har safar YANGIDAN boshlaydi — bot nega to'xtaganini ko'rsatadigan
# oxirgi yozuvlar (masalan Python traceback) yo'qolmasligi uchun avvalgisini arxivlaymiz.
# So'nggi 20 ta arxiv saqlanadi. (Ilova loglarining o'zi logs\bot.log da aylanib turadi.)
try {
    $LogDir = Join-Path $ProjectDir "logs"
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    if ((Test-Path $ErrLog) -and (Get-Item $ErrLog).Length -gt 0) {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        Move-Item -Path $ErrLog -Destination (Join-Path $LogDir "run.err.$stamp.log") -Force
    }
    Get-ChildItem -Path $LogDir -Filter "run.err.*.log" | Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 20 | Remove-Item -Force -ErrorAction SilentlyContinue
} catch {
    # Arxivlab bo'lmasa ham bot baribir ishga tushirilishi kerak
}

Start-Process -FilePath $Python -ArgumentList "-m", "app.main" -WorkingDirectory $ProjectDir `
    -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -WindowStyle Hidden

Write-Output "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'): bot ishlamayotgani aniqlandi, qayta ishga tushirildi"
