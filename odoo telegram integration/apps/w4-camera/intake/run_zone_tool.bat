@echo off
rem W4-01: eshik zonasi vositasi. Brauzerda http://127.0.0.1:8765/ ochiladi.
rem To'xtatish: shu oynada Ctrl+C yoki oynani yoping.
cd /d "%~dp0"
set "PY=C:\Program Files\Python312\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" zone_server.py
pause
