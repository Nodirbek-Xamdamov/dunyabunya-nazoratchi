# P1-1: Bot jarayonini ishga tushiradi (Task Scheduler orqali chaqiriladi).
# Task Scheduler'ning "agar vazifa muvaffaqiyatsiz tugasa qayta ishga
# tushirish" sozlamasi bilan birga bu systemd'dagi Restart=always'ning
# Windows ekvivalenti bo'ladi.
#
# run.out.log/run.err.log'ga yozadi — har ishga tushganda ESKI logni saqlab
# qolish uchun ustiga yozmasdan qo'shib boradi (Add-Content), aks holda har
# qayta tiklanishda oldingi xato tarixi yo'qolib qolardi.

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$OutLog = Join-Path $ProjectDir "run.out.log"
$ErrLog = Join-Path $ProjectDir "run.err.log"

# DIQQAT: `python ... 2>> file` kabi PowerShell'ning o'zi orqali qayta
# yo'naltirish ishlatilmaydi — PS 5.1'da native dastur stderr'ini shu tarzda
# qayta yo'naltirish har bir qatorni ErrorRecord'ga o'rab, butun skriptni
# "xato" deb to'xtatib qo'yadi (garchi dastur normal ishlayotgan bo'lsa ham,
# chunki bu ilova oddiy INFO loglarini ham stderr'ga yozadi). Shuning uchun
# Start-Process -RedirectStandardOutput/-Error ishlatiladi (haqiqiy fayl
# tavsifi darajasida yo'naltirish, PowerShell error-stream orqali emas).
# Har ishga tushganda log fayllar YANGILANADI (avvalgi qayta ishga
# tushirishlarda ham shunday bo'lgan) — eski xato tarixi kerak bo'lsa
# alohida arxivlash qo'shish mumkin.

$p = Start-Process -FilePath $Python -ArgumentList "-m", "app.main" -WorkingDirectory $ProjectDir `
    -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -NoNewWindow -PassThru -Wait
exit $p.ExitCode
