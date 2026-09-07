@echo off
rem Start one more instance without touching the running ones (restart.bat kills them all).
rem Python is located by find_python.bat (MM_PYTHON, .venv next to the repo, C:\mm_venv, PATH).
rem pythonw.exe = no console window; log goes to %APPDATA%\MortalManager\mortalmanager.log
setlocal
cd /d "%~dp0"
call "%~dp0find_python.bat" || (pause & exit /b 1)
start "" "%PYW%" -m src.main
endlocal
