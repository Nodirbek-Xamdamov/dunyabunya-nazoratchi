# Bot ishlab turganini tekshiradi; ishlamasa yoki qotib qolgan bo'lsa qayta ishga tushiradi.
# Task Scheduler har 1 daqiqada ishga tushiradi (install_watchdog.ps1). Qo'lda ham ishga tushirish mumkin.
#
# "Ishlayapti" = data\docbot.pid dagi jarayon tirik, u haqiqatan "docbot ... run" (PID boshqa dasturga
# o'tib ketgan bo'lishi mumkin) va data\heartbeat oxirgi 3 daqiqada yangilangan. Bot o'zi bir nusxada
# ishlashini qulf fayli bilan kafolatlaydi, shuning uchun ortiqcha ishga tushirish zarar qilmaydi.

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$DataDir = Join-Path $ProjectDir "data"
$LogDir = Join-Path $DataDir "logs"
$PidFile = Join-Path $DataDir "docbot.pid"
$Heartbeat = Join-Path $DataDir "heartbeat"
$WatchLog = Join-Path $LogDir "watchdog.log"
$StaleSeconds = 180

function Write-Log([string]$Message) {
    Add-Content -Path $WatchLog -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message" -Encoding UTF8
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$botPid = $null
if (Test-Path $PidFile) {
    $text = (Get-Content $PidFile -Raw -ErrorAction SilentlyContinue)
    if ($text -and $text.Trim() -match '^\d+$') { $botPid = [int]$text.Trim() }
}
if ($botPid) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$botPid" -ErrorAction SilentlyContinue
    if ($proc -and $proc.CommandLine -match '-m\s+docbot\b.*\brun\b') {
        $age = if (Test-Path $Heartbeat) { ((Get-Date) - (Get-Item $Heartbeat).LastWriteTime).TotalSeconds } else { 99999 }
        if ($age -lt $StaleSeconds) { exit 0 }  # ishlayapti
        Write-Log ("heartbeat {0:N0}s eski: jarayon {1} qotib qolgan, to'xtatilmoqda" -f $age, $botPid)
        Stop-Process -Id $botPid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
    }
}

# Oldingi ishga tushirishning konsol chiqishi (odatda bo'sh; yiqilish traceback'i shu yerda qoladi)
$ErrLog = Join-Path $LogDir "run.err.log"
if (Test-Path $ErrLog) { Move-Item -Force $ErrLog (Join-Path $LogDir "run.err.prev.log") }

Start-Process -FilePath $Python -ArgumentList @("-m", "docbot", "run") -WorkingDirectory $ProjectDir -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogDir "run.out.log") -RedirectStandardError $ErrLog
Write-Log "bot ishga tushirildi"
