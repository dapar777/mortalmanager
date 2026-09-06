@echo off
rem Start one more instance without touching the running ones (restart.bat kills them all).
rem pythonw.exe = no console window; log goes to %APPDATA%\MortalManager\mortalmanager.log
cd /d "%~dp0"
start "" C:\mm_venv\Scripts\pythonw.exe -m src.main
