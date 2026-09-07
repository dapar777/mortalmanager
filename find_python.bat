@echo off
rem Locates a Python that has PySide6 and sets PYW (pythonw.exe) for start.bat / restart.bat.
rem Order: MM_PYTHON env var, .venv / venv next to the repo, C:\mm_venv, then pythonw.exe on PATH.
rem Returns exit code 1 (and prints a hint) when nothing usable is found.
set "PYW="
set "_MM_ROOT=%~dp0"

if defined MM_PYTHON call :try "%MM_PYTHON%"
if defined PYW exit /b 0

for %%P in ("%_MM_ROOT%.venv\Scripts\pythonw.exe" "%_MM_ROOT%venv\Scripts\pythonw.exe" "C:\mm_venv\Scripts\pythonw.exe") do (
    if not defined PYW call :try %%P
)
if defined PYW exit /b 0

for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do (
    if not defined PYW call :try "%%P"
)
if defined PYW exit /b 0

echo MortalManager: no Python with PySide6 found.
echo   Create a venv next to the project and install the requirements:
echo     py -m venv "%_MM_ROOT%.venv"
echo     "%_MM_ROOT%.venv\Scripts\pip" install -r "%_MM_ROOT%requirements.txt"
echo   or point MM_PYTHON at a pythonw.exe that has PySide6.
exit /b 1

:try
set "_CAND=%~1"
if not exist "%_CAND%" exit /b 1
rem pythonw has no console; run the import check with the console python next to it
set "_CHECK=%_CAND:pythonw.exe=python.exe%"
if not exist "%_CHECK%" set "_CHECK=%_CAND%"
"%_CHECK%" -c "import PySide6" >nul 2>&1 || exit /b 1
set "PYW=%_CAND%"
exit /b 0
