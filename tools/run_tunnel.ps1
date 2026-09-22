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
$envLines = Get-Content $EnvPath
$newLines = $envLines | ForEach-Object {
    if ($_ -match '^PUBLIC_BASE_URL=') { "PUBLIC_BASE_URL=$publicUrl" } else { $_ }
}
Set-Content -Path $EnvPath -Value $newLines -Encoding utf8

Write-Output ".env yangilandi. Botni qayta ishga tushirish uchun app/main.py qayta start qilinishi kerak."
