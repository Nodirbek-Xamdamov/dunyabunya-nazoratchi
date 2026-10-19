<#
dunyabunya monitoring dashboard'ini ishga tushiradi (Streamlit, dashboard\.venv ichida).

  powershell -ExecutionPolicy Bypass -File dashboard\run_dashboard.ps1                 # haqiqiy baza (faqat o'qish)
  powershell -ExecutionPolicy Bypass -File dashboard\run_dashboard.ps1 -Demo           # demo baza
  powershell -ExecutionPolicy Bypass -File dashboard\run_dashboard.ps1 -Demo -RebuildDemo
  powershell -ExecutionPolicy Bypass -File dashboard\run_dashboard.ps1 -Stop           # shu skript ishga tushirgan dashboard'ni to'xtatish

Skript faqat dashboard papkasi ichidagi fayllarga tegadi: .venv, logs, demo bazasi.
#>
param([switch]$Demo, [switch]$RebuildDemo, [switch]$Stop)

$ErrorActionPreference = "Stop"
$dash = $PSScriptRoot
$venvPython = Join-Path $dash ".venv\Scripts\python.exe"
$logs = Join-Path $dash "logs"
$pidFile = Join-Path $logs "dashboard.pid"
$localHosts = @("127.0.0.1", "localhost", "::1")

function Read-DashboardEnv {
    $values = @{}
    $file = Join-Path $dash ".env"
    if (Test-Path -LiteralPath $file) {
        foreach ($line in Get-Content -LiteralPath $file -Encoding UTF8) {
            $text = $line.Trim()
            if (-not $text -or $text.StartsWith("#") -or -not $text.Contains("=")) { continue }
            $key, $value = $text.Split("=", 2)
            $values[$key.Trim()] = $value.Trim().Trim('"').Trim("'")
        }
    }
    return $values
}

function Stop-Dashboard {
    if (-not (Test-Path -LiteralPath $pidFile)) { Write-Host "Dashboard bu skript bilan ishga tushirilmagan (PID fayli yo'q)."; return }
    $procId = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
    if ($proc -and $proc.CommandLine -match "streamlit" -and $proc.CommandLine -match "app\.py") {
        # venv'dagi python.exe asosiy Python'ni bola jarayon sifatida ishga tushiradi -- ikkalasini to'xtatamiz
        foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$procId" -ErrorAction SilentlyContinue)) {
            if ($child.CommandLine -match "streamlit") { Stop-Process -Id $child.ProcessId -Force }
        }
        Stop-Process -Id $procId -Force
        Write-Host "Dashboard to'xtatildi (PID $procId)."
    } else {
        Write-Host "PID $procId dashboard jarayoni emas yoki allaqachon to'xtagan -- hech narsa to'xtatilmadi."
    }
    Remove-Item -LiteralPath $pidFile -Force
}

if ($Stop) { Stop-Dashboard; exit 0 }

# 1) Alohida muhit: dashboard\.venv (botning .venv'iga hech narsa o'rnatilmaydi)
if (-not (Test-Path -LiteralPath $venvPython)) {
    $candidates = @("C:\Program Files\Python312\python.exe")
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found) { $candidates += $found.Source }
    $basePython = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $basePython) { throw "Python 3.12 topilmadi." }
    & $basePython -m venv (Join-Path $dash ".venv")
}
$requirements = Join-Path $dash "requirements.txt"
$stamp = Join-Path $dash ".venv\requirements.sha256"
$hash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
if (-not (Test-Path -LiteralPath $stamp) -or (Get-Content -LiteralPath $stamp -Raw).Trim() -ne $hash) {
    & $venvPython -m pip install --disable-pip-version-check -r $requirements
    if ($LASTEXITCODE -ne 0) { throw "Kutubxonalarni o'rnatishda xato." }
    Set-Content -LiteralPath $stamp -Value $hash -Encoding ascii
}

# 2) Manzil, port va parol (dashboard\.env)
$config = Read-DashboardEnv
$listen = if ($config["DASHBOARD_HOST"]) { $config["DASHBOARD_HOST"] } else { "127.0.0.1" }
$port = if ($config["DASHBOARD_PORT"]) { [int]$config["DASHBOARD_PORT"] } else { 8502 }
if (($localHosts -notcontains $listen) -and -not $config["DASHBOARD_PASSWORD"]) {
    throw "DASHBOARD_HOST=$listen, lekin DASHBOARD_PASSWORD bo'sh. Tashqi manzil uchun parol majburiy -- ishga tushirish rad etildi."
}
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $port band. dashboard\.env faylida DASHBOARD_PORT ni boshqa portga o'zgartiring (8501 bu serverda band)."
}

# 3) Demo rejim
if ($Demo) {
    $env:DASHBOARD_DEMO_MODE = "1"
    $demoDb = Join-Path $dash "demo\demo_monitor.db"
    $stale = -not (Test-Path -LiteralPath $demoDb) -or ((Get-Date) - (Get-Item -LiteralPath $demoDb).LastWriteTime).TotalHours -gt 24
    if ($RebuildDemo -or $stale) {
        Write-Host "Demo baza yaratilmoqda..."
        & $venvPython (Join-Path $dash "demo\build_demo.py")
        if ($LASTEXITCODE -ne 0) { throw "Demo bazani yaratib bo'lmadi." }
    }
}

# 4) Ishga tushirish (oldingi log .prev ga ko'chiriladi)
New-Item -ItemType Directory -Force $logs | Out-Null
foreach ($name in "dashboard.out.log", "dashboard.err.log") {
    $file = Join-Path $logs $name
    if (Test-Path -LiteralPath $file) { Move-Item -LiteralPath $file -Destination "$file.prev" -Force }
}
$arguments = @("-m", "streamlit", "run", "app.py", "--server.address", $listen, "--server.port", "$port",
               "--server.headless", "true", "--browser.gatherUsageStats", "false")
$process = Start-Process -FilePath $venvPython -ArgumentList $arguments -WorkingDirectory $dash -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs "dashboard.out.log") -RedirectStandardError (Join-Path $logs "dashboard.err.log") -PassThru
Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ascii
for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Milliseconds 500
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { break }
}
$mode = if ($Demo) { "DEMO" } else { "haqiqiy baza, faqat o'qish" }
Write-Host "Dashboard ($mode): http://${listen}:$port   (PID $($process.Id); loglar: $logs)"
