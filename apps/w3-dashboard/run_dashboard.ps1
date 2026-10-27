<#
W3 dashboard'ini ishga tushiradi (Streamlit, apps\w3-dashboard\.venv ichida). Faqat 127.0.0.1 (D-15).

  powershell -ExecutionPolicy Bypass -File "apps\w3-dashboard\run_dashboard.ps1"         # ishga tushirish
  powershell -ExecutionPolicy Bypass -File "apps\w3-dashboard\run_dashboard.ps1" -Stop   # shu skript ishga tushirganini to'xtatish

Port va baza: app.toml ([server] port, [db] database). Parol secrets\postgres.env dan dastur o'zi yuklagich
orqali oladi; bu skript sirlarni o'qimaydi. Ishlab turgan prototip (dashboard\, 8502) ga tegilmaydi.
#>
param([switch]$Stop)

$ErrorActionPreference = "Stop"
$dash = $PSScriptRoot
$venvPython = Join-Path $dash ".venv\Scripts\python.exe"
$logs = Join-Path $dash "logs"
$pidFile = Join-Path $logs "dashboard.pid"
$listen = "127.0.0.1"

function Stop-Dashboard {
    if (-not (Test-Path -LiteralPath $pidFile)) { Write-Host "W3 dashboard bu skript bilan ishga tushirilmagan (PID fayli yo'q)."; return }
    $procId = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
    if ($proc -and $proc.CommandLine -match "streamlit" -and $proc.CommandLine -match "app\.py" -and $proc.ExecutablePath -like "$dash*") {
        foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$procId" -ErrorAction SilentlyContinue)) {
            if ($child.CommandLine -match "streamlit") { Stop-Process -Id $child.ProcessId -Force }
        }
        Stop-Process -Id $procId -Force
        Write-Host "W3 dashboard to'xtatildi (PID $procId)."
    } else {
        Write-Host "PID $procId W3 dashboard jarayoni emas yoki allaqachon to'xtagan -- hech narsa to'xtatilmadi."
    }
    Remove-Item -LiteralPath $pidFile -Force
}

if ($Stop) { Stop-Dashboard; exit 0 }

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "apps\w3-dashboard\.venv yo'q. Yaratish: python -m venv .venv; .venv\Scripts\python -m pip install -r requirements.txt; .venv\Scripts\python -m pip install -e ..\..\shared"
}

# Port app.toml dan ([server] port)
$port = 8503
$toml = Get-Content -LiteralPath (Join-Path $dash "app.toml") -Encoding UTF8
$inServer = $false
foreach ($line in $toml) {
    if ($line -match '^\s*\[(.+)\]') { $inServer = ($Matches[1] -eq "server"); continue }
    if ($inServer -and $line -match '^\s*port\s*=\s*(\d+)') { $port = [int]$Matches[1] }
}
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $port band. app.toml da [server] port ni o'zgartiring (8501, 8502 bu serverda band)."
}

New-Item -ItemType Directory -Force $logs | Out-Null
foreach ($name in "dashboard.out.log", "dashboard.err.log") {
    $file = Join-Path $logs $name
    if (Test-Path -LiteralPath $file) { Move-Item -LiteralPath $file -Destination "$file.prev" -Force }
}
$arguments = @("-E", "-m", "streamlit", "run", "app.py", "--server.address", $listen, "--server.port", "$port",
               "--server.headless", "true", "--browser.gatherUsageStats", "false")
$process = Start-Process -FilePath $venvPython -ArgumentList $arguments -WorkingDirectory $dash -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs "dashboard.out.log") -RedirectStandardError (Join-Path $logs "dashboard.err.log") -PassThru
Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ascii
for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Milliseconds 500
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { break }
}
Write-Host "W3 dashboard: http://${listen}:$port   (PID $($process.Id); loglar: $logs)"
