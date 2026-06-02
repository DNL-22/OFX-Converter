@echo off
REM ============================================================
REM   OFX-Converter - Windows build script
REM   Produces dist\OFX-Converter.exe (single-file, no console)
REM ============================================================

setlocal

echo.
echo  OFX-Converter - building Windows executable
echo  -------------------------------------------
echo.

REM --- Check Python ---
where python >nul 2>nul
if errorlevel 1 (
    echo  [X] Python not found. Install from https://python.org and re-run.
    exit /b 1
)

REM --- Install runtime + build deps ---
echo  [1/3] Installing dependencies...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
python -m pip install --quiet pyinstaller
if errorlevel 1 (
    echo  [X] Dependency install failed.
    exit /b 1
)

REM --- Clean previous build ---
echo  [2/3] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

REM --- Run PyInstaller ---
echo  [3/3] Running PyInstaller...
python -m PyInstaller --noconfirm OFX-Converter.spec
if errorlevel 1 (
    echo  [X] PyInstaller failed.
    exit /b 1
)

echo.
if exist dist\OFX-Converter.exe (
    echo  [OK] Built: dist\OFX-Converter.exe
    for %%A in (dist\OFX-Converter.exe) do echo       Size: %%~zA bytes
    echo.
    echo  Double-click the .exe to test, then distribute it to the team.
) else (
    echo  [X] Expected dist\OFX-Converter.exe but it was not produced.
    exit /b 1
)

endlocal
