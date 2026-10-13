# Bot (python -m app.bot) ishlab turganini tekshiradi; ishlamayotgan bo'lsa qayta ishga tushiradi.
# Task Scheduler'da har daqiqa va kompyuter yonganda ishga tushiriladi (README 4.5-band).
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.ExecutablePath -eq $python -and $_.CommandLine -match '-m app\.bot\s*$' }
if ($running) { exit 0 }

$logs = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logs | Out-Null
foreach ($name in "bot.err.log", "bot.out.log") {
    $file = Join-Path $logs $name
    if (Test-Path $file) { Copy-Item $file "$file.prev" -Force }   # yangi ishga tushish logni ustidan yozadi
}
Start-Process -FilePath $python -ArgumentList "-m", "app.bot" -WorkingDirectory $root `
    -RedirectStandardOutput (Join-Path $logs "bot.out.log") `
    -RedirectStandardError (Join-Path $logs "bot.err.log") -WindowStyle Hidden
