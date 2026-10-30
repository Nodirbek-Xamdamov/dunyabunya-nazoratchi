# Kuzatuv xizmatini fonda ishga tushiradi (oyna ochilmaydi). To'xtatish: data\STOP faylini yarating.
$dir = $PSScriptRoot
$env:YOLO_OFFLINE = '1'
New-Item -ItemType Directory -Force "$dir\data\logs" | Out-Null
Start-Process -WindowStyle Hidden -WorkingDirectory $dir -FilePath "$dir\.venv\Scripts\python.exe" `
  -ArgumentList '-m', 'nazorat.xizmat' `
  -RedirectStandardOutput "$dir\data\logs\xizmat.console.log" -RedirectStandardError "$dir\data\logs\xizmat.console.err.log"
