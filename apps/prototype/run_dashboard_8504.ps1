# Prototip dashboard: 127.0.0.1:8504, prototype.db ni ko'rsatadi (2026-10-27).
# Asl dashboard\.venv interpretatori ishlatiladi (unga hech narsa o'rnatilmaydi). Agent botlari ishga tushirilmaydi.
# 8502 (eski dashboard) ga tegmaydi.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$dash = Join-Path $here "dashboard"
$python = Join-Path $here "..\..\dashboard\.venv\Scripts\python.exe"
$logs = Join-Path $here "logs"
New-Item -ItemType Directory -Force $logs | Out-Null
if (Get-NetTCPConnection -State Listen -LocalPort 8504 -ErrorAction SilentlyContinue) { Write-Host "8504 band -- ishga tushirilmadi"; exit 1 }
$p = Start-Process -FilePath $python -WorkingDirectory $dash -WindowStyle Hidden -PassThru `
    -ArgumentList @("-m", "streamlit", "run", "app.py", "--server.address", "127.0.0.1", "--server.port", "8504",
                    "--server.headless", "true", "--browser.gatherUsageStats", "false") `
    -RedirectStandardOutput (Join-Path $logs "dashboard_8504.out.log") -RedirectStandardError (Join-Path $logs "dashboard_8504.err.log")
Set-Content -Path (Join-Path $logs "dashboard_8504.pid") -Value $p.Id
Write-Host "Prototip dashboard: http://127.0.0.1:8504 (PID $($p.Id))"
