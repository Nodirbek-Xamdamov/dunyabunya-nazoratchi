# P0-4: dunyabunya CEO agent — PostgreSQL yordamchi bazaning kunlik zaxira nusxasi.
#
# .env dagi DATABASE_URL'ni o'qib, pg_dump orqali custom-format (-F c, siqilgan,
# pg_restore bilan tiklanadigan) backup yasaydi va RetentionDays'dan eski
# nusxalarni o'chiradi. Windows Task Scheduler orqali har kuni ishga tushiriladi
# (cron ekvivalenti — setup_backup_task.ps1 buni bir marta ro'yxatga oladi).
#
# Qo'lda ishga tushirish: powershell -ExecutionPolicy Bypass -File backup_db.ps1

param(
    [string]$BackupDir = "E:\Backups\dunyabunya_db",
    [int]$RetentionDays = 14
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$EnvPath = Join-Path $ProjectDir ".env"
$PgDump = "C:\Program Files\PostgreSQL\17\bin\pg_dump.exe"

if (-not (Test-Path $EnvPath)) {
    throw ".env topilmadi: $EnvPath"
}
if (-not (Test-Path $PgDump)) {
    throw "pg_dump topilmadi: $PgDump"
}

$line = Get-Content $EnvPath | Where-Object { $_ -match '^DATABASE_URL=' } | Select-Object -First 1
if (-not $line) {
    throw ".env ichida DATABASE_URL topilmadi"
}
$dbUrl = $line -replace '^DATABASE_URL=', ''

# postgresql+asyncpg://user:pass@host:port/dbname
if ($dbUrl -notmatch '^postgresql(\+\w+)?://([^:]+):([^@]+)@([^:/]+):(\d+)/(.+)$') {
    throw "DATABASE_URL formatini aniqlab bo'lmadi"
}
$dbUser = $Matches[2]
$dbPass = $Matches[3]
$dbHostName = $Matches[4]
$dbPort = $Matches[5]
$dbName = $Matches[6]

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outFile = Join-Path $BackupDir "${dbName}_$timestamp.dump"

$env:PGPASSWORD = $dbPass
try {
    & $PgDump -h $dbHostName -p $dbPort -U $dbUser -F c -f $outFile $dbName
    if ($LASTEXITCODE -ne 0) {
        throw "pg_dump muvaffaqiyatsiz tugadi (exit code $LASTEXITCODE)"
    }
} finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}

$size = (Get-Item $outFile).Length
Write-Output "Backup OK: $outFile ($size bayt)"

# Eski backuplarni tozalash
$cutoff = (Get-Date).AddDays(-$RetentionDays)
$removed = 0
Get-ChildItem $BackupDir -Filter "${dbName}_*.dump" | Where-Object { $_.LastWriteTime -lt $cutoff } | ForEach-Object {
    Remove-Item $_.FullName -Force
    $removed++
}
if ($removed -gt 0) {
    Write-Output "$removed ta $RetentionDays kundan eski backup o'chirildi"
}
