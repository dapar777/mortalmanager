@echo off
rem Restart: kill every running instance (python.exe and windowless pythonw.exe with "src.main"
rem on the command line), then start a fresh one. Python is located by find_python.bat
rem (MM_PYTHON, .venv next to the repo, C:\mm_venv, PATH); wmic is not used (gone on newer Windows 11).
rem pythonw.exe = no console window; log goes to %APPDATA%\UltimateCommander\ultimatecommander.log
setlocal
cd /d "%~dp0"
call "%~dp0find_python.bat" || (pause & exit /b 1)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*src.main*' -and ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 1 /nobreak >nul
start "" "%PYW%" -m src.main
endlocal
