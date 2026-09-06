@echo off
taskkill /F /IM python.exe /FI "WINDOWTITLE eq mortalmanager*" 2>nul
taskkill /F /FI "COMMANDLINE eq *src.main*" /IM python.exe 2>nul
wmic process where "CommandLine like '%%src.main%%'" delete 2>nul >nul
timeout /t 1 /nobreak >nul
start "" C:\mm_venv\Scripts\python.exe -m src.main
