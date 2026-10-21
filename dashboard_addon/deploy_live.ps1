<#
«Yangi kamera» bo'limini ISHLAB TURGAN CEO paneliga o'rnatadi.

  powershell -ExecutionPolicy Bypass -File D:\Demos\Kamera\dashboard_addon\deploy_live.ps1 -Check   # faqat tekshirish
  powershell -ExecutionPolicy Bypass -File D:\Demos\Kamera\dashboard_addon\deploy_live.ps1          # o'rnatish

Faqat egasining ruxsati bilan ishga tushiriladi: panel (va u bilan birga agentlarning Telegram botlari)
~15 soniya to'xtaydi. Avval panelning zip zaxirasi olinadi. Qaytarish: install.py --uninstall yoki zip zaxira.
#>
param([switch]$Check)
$ErrorActionPreference = "Stop"
$dash = "D:\Demos\odoo telegram integration\dashboard"
$addon = "D:\Demos\Kamera\dashboard_addon"
$py = "C:\Program Files\Python312\python.exe"

& $py -I "$addon\install.py" $dash --check
if ($LASTEXITCODE -ne 0) { throw "app.py kutilgan ko'rinishda emas - hech narsa o'zgartirilmadi." }
if ($Check) { return }

$stamp = Get-Date -Format "yyyy-MM-dd_HH-mm"
& $py -I "$addon\backup.py" $dash "D:\Demos\odoo telegram integration\backups\dashboard_before_kamera_install_$stamp.zip"
if ($LASTEXITCODE -ne 0) { throw "Zaxira olinmadi - o'rnatish to'xtatildi." }

& powershell -ExecutionPolicy Bypass -File "$dash\run_dashboard.ps1" -Stop
& $py -I "$addon\install.py" $dash
if ($LASTEXITCODE -ne 0) { Write-Host "O'rnatishda xato - panel eski holida qayta yoqiladi." }
& powershell -ExecutionPolicy Bypass -File "$dash\run_dashboard.ps1"

$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        if ((Invoke-WebRequest -Uri "http://127.0.0.1:8502/_stcore/health" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) { $ok = $true; break }
    } catch { }
    Start-Sleep -Seconds 1
}
Write-Host "Panel: $(if ($ok) { 'ishlayapti' } else { 'JAVOB BERMAYAPTI - logs\dashboard.err.log ni ko''ring' })"
