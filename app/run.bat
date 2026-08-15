@echo off
setlocal
cd /d "%~dp0"

set "VENV=.venv"
set "PYW=%VENV%\Scripts\pythonw.exe"
set "PY=%VENV%\Scripts\python.exe"

if not exist "%PY%" (
    echo Creating virtual environment...
    py -3 -m venv "%VENV%" 2>nul || python -m venv "%VENV%"
)

if not exist "%PY%" (
    echo.
    echo ERROR: Could not create a Python environment. Install Python 3.10+ from
    echo python.org and try again.
    pause
    exit /b 1
)

echo Installing / updating dependencies (first run may take a minute)...
"%PY%" -m pip install --upgrade pip --quiet --trusted-host pypi.org --trusted-host files.pythonhosted.org
"%PY%" -m pip install -r requirements.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org
if errorlevel 1 (
    echo.
    echo ERROR: Failed to install dependencies. See messages above.
    pause
    exit /b 1
)

echo Launching Fistgirl...
start "" "%PYW%" app.py
exit /b 0
