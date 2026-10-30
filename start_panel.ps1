# Panelni ishga tushiradi: http://127.0.0.1:8520
$dir = $PSScriptRoot
New-Item -ItemType Directory -Force "$dir\data\logs" | Out-Null
Start-Process -WindowStyle Hidden -WorkingDirectory $dir -FilePath "$dir\.venv\Scripts\python.exe" `
  -ArgumentList '-m', 'streamlit', 'run', 'app.py', '--server.port', '8520', '--server.address', '127.0.0.1', '--server.headless', 'true', '--browser.gatherUsageStats', 'false' `
  -RedirectStandardOutput "$dir\data\logs\panel.log" -RedirectStandardError "$dir\data\logs\panel.err.log"
