@echo off
setlocal

set "PYTHON=%~1"
if "%PYTHON%"=="" set "PYTHON=python"

set "BUILD_ENV=%~2"
if "%BUILD_ENV%"=="" set "BUILD_ENV=%SystemDrive%\LSV-Origin-Auto-Build-Env"

pushd "%~dp0"
if not exist "%BUILD_ENV%\Scripts\python.exe" (
    "%PYTHON%" -m venv "%BUILD_ENV%"
    if errorlevel 1 (
        popd
        exit /b 1
    )
)

"%BUILD_ENV%\Scripts\python.exe" -m pip install -r "requirements-build.txt"
if errorlevel 1 (
    popd
    exit /b 1
)

"%BUILD_ENV%\Scripts\python.exe" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name "LSV-Origin-Auto" ^
    --specpath "build" ^
    --workpath "build\pyinstaller" ^
    --distpath "dist" ^
    --add-data "%~dp0momonga.jpg;." ^
    --collect-all originpro ^
    --collect-all OriginExt ^
    "forrealgui.py"

if errorlevel 1 (
    popd
    exit /b 1
)

echo Build complete: %~dp0dist\LSV-Origin-Auto.exe
popd
