@echo off
rem Kill running instances (console python.exe as well as windowless pythonw.exe)
taskkill /F /FI "COMMANDLINE eq *src.main*" /IM python.exe 2>nul
taskkill /F /FI "COMMANDLINE eq *src.main*" /IM pythonw.exe 2>nul
wmic process where "CommandLine like '%%src.main%%'" delete 2>nul >nul
timeout /t 1 /nobreak >nul
rem pythonw.exe = no console window; log goes to %APPDATA%\MortalManager\mortalmanager.log
start "" C:\mm_venv\Scripts\pythonw.exe -m src.main
