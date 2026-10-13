# Cloudflare Quick Tunnel'ni ishga tushiradi (hisobsiz/domensiz) va
# localhost:8002'ni global https://*.trycloudflare.com manziliga ulaydi.
#
# DIQQAT: bu "quick tunnel" — har safar QAYTA ishga tushirilganda YANGI
# tasodifiy manzil beriladi. Shuning uchun bu skript manzilni avtomatik
# .env'dagi PUBLIC_BASE_URL'ga yozadi va botni qayta ishga tushiradi —
# hisobotlar havolasi doim YANGI manzilga mos bo'lib qolishi uchun.

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$Cloudflared = Join-Path $PSScriptRoot "cloudflared.exe"
$LogFile = Join-Path $PSScriptRoot "cloudflared.log"
$EnvPath = Join-Path $ProjectDir ".env"

# Eski tunnel jarayonini to'xtatish (agar bor bo'lsa)
Get-Process -Name "cloudflared" -ErrorAction SilentlyContinue | Stop-Process -Force

Remove-Item $LogFile -ErrorAction SilentlyContinue
Start-Process -FilePath $Cloudflared -ArgumentList "tunnel", "--url", "http://localhost:8002" `
    -RedirectStandardOutput $LogFile -RedirectStandardError $LogFile -WindowStyle Hidden

# Manzil log'da paydo bo'lishini kutamiz (odatda 5-10 soniya)
$publicUrl = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $LogFile) {
        $match = Select-String -Path $LogFile -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
        if ($match) {
            $publicUrl = $match.Matches[0].Value
            break
        }
    }
}

if (-not $publicUrl) {
    throw "Tunnel manzili 30 soniyada topilmadi — $LogFile ni tekshiring"
}

Write-Output "Tunnel manzili: $publicUrl"

# .env'dagi PUBLIC_BASE_URL'ni yangilaymiz
$envLines = Get-Content $EnvPath -Encoding UTF8
$oldLine = $envLines | Where-Object { $_ -match '^PUBLIC_BASE_URL=' } | Select-Object -First 1
if ($oldLine -eq "PUBLIC_BASE_URL=$publicUrl") {
    Write-Output "Manzil o'zgarmagan — .env va bot tegilmadi."
    exit 0
}
$newLines = $envLines | ForEach-Object {
    if ($_ -match '^PUBLIC_BASE_URL=') { "PUBLIC_BASE_URL=$publicUrl" } else { $_ }
}
# PS 5.1'da "-Encoding utf8" BOM qo'shadi — .env'ning birinchi kaliti buzilmasligi uchun BOM'siz yozamiz
[System.IO.File]::WriteAllLines($EnvPath, [string[]]$newLines, (New-Object System.Text.UTF8Encoding($false)))
Write-Output ".env yangilandi."

# Quick Tunnel har qayta ishga tushganda (masalan server reboot) YANGI manzil oladi — bot
# PUBLIC_BASE_URL'ni faqat startda o'qiydi, shuning uchun eski (o'lik) havolalarni yubormasligi
# uchun uni to'xtatamiz; bot_watchdog (har daqiqa) uni yangi .env bilan qayta ishga tushiradi.
# Filtr ataylab "-m app.main" bilan tugaydigan qatorga — db_web_chatbot (app.main:app) tegilmaydi.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match '-m\s+app\.main\s*$' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Output "Bot to'xtatildi — watchdog 1 daqiqa ichida yangi manzil bilan qayta ishga tushiradi."
