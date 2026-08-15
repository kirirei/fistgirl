@echo off
REM ---------------------------------------------------------------------------
REM  Rebuild the portable Fistgirl.exe from the standalone Python source (.\app).
REM  Produces .\dist\Fistgirl\Fistgirl.exe (onedir). Requires Python 3.10+.
REM  (CI does the same thing in .github/workflows/release.yml for releases.)
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "SRC=app"
set "VENV=_buildenv"

if not exist "%SRC%\app.py" (
    echo ERROR: source not found at %SRC%\app.py
    pause
    exit /b 1
)

echo Creating build virtual environment...
py -3 -m venv "%VENV%" 2>nul || python -m venv "%VENV%"
if not exist "%VENV%\Scripts\python.exe" (
    echo ERROR: could not create a Python environment. Install Python 3.10+.
    pause
    exit /b 1
)

echo Installing dependencies + PyInstaller...
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip --quiet --trusted-host pypi.org --trusted-host files.pythonhosted.org
"%VENV%\Scripts\python.exe" -m pip install -r "%SRC%\requirements.txt" pyinstaller --trusted-host pypi.org --trusted-host files.pythonhosted.org
if errorlevel 1 ( echo ERROR: dependency install failed. & pause & exit /b 1 )

echo Building Fistgirl.exe (this takes a minute)...
"%VENV%\Scripts\python.exe" -m PyInstaller --noconfirm --name Fistgirl --windowed ^
    --collect-all customtkinter --collect-all nodriver ^
    --distpath "dist" --workpath "%VENV%\build" --specpath "%VENV%" ^
    "%SRC%\app.py"
if errorlevel 1 ( echo ERROR: PyInstaller build failed. & pause & exit /b 1 )

echo.
echo Done. The portable app is in:  .\dist\Fistgirl\Fistgirl.exe
pause
