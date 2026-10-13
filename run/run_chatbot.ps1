# Watchdog: db_web_chatbot xizmatini doimiy ishlab turishini ta'minlaydi —
# agar uvicorn nimadir sababdan yiqilsa, avtomatik qayta ishga tushiradi.
$ErrorActionPreference = "Continue"
$root = "D:\Demos\db_web_chatbot"
$python = Join-Path $root ".venv\Scripts\python.exe"
$logDir = Join-Path $root "run\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

while ($true) {
    $ts = Get-Date -Format "yyyyMMdd_HHmmss"
    $logFile = Join-Path $logDir "uvicorn_$ts.log"
    Set-Location $root
    & $python -m uvicorn app.main:app --host 0.0.0.0 --port 8003 *>> $logFile
    Add-Content -Path (Join-Path $logDir "watchdog.log") -Value "$(Get-Date -Format o) - uvicorn exited, restarting in 5s"
    Start-Sleep -Seconds 5
}
