@echo off
setlocal
set "APP_DIR=%~dp0"
set "LOG=%~dp0run.log"
set "PHASE=launcher initialization"
set "APP_VERSION=unknown"
set "STARTUP_MODE=unknown"
if exist "%~dp0VERSION" set /p "APP_VERSION="<"%~dp0VERSION"
if exist "%~dp0uv.exe" set "STARTUP_MODE=portable"
if not exist "%~dp0uv.exe" if exist "%~dp0.git" set "STARTUP_MODE=developer checkout"

> "%LOG%" (
  echo Effigy startup
  echo Started: %date% %time%
  echo Version: %APP_VERSION%
  echo Startup mode: %STARTUP_MODE%
  echo Log: "%LOG%"
  echo This log covers launcher bootstrap phases.
)
if errorlevel 1 goto log_unavailable

echo Startup phase and setup output are being recorded in "%LOG%".
call :phase "launcher initialization"
cd /d "%APP_DIR%"
if errorlevel 1 call :fail "%errorlevel%" "launcher initialization"
if errorlevel 1 exit /b %errorlevel%

call :phase "uv discovery"
where uv >nul 2>nul
if errorlevel 1 goto uv_missing
>> "%LOG%" echo uv was found on PATH.

call :phase "base Python environment"
call uv sync --frozen >> "%LOG%" 2>&1
if errorlevel 1 call :fail "%errorlevel%" "base Python environment"
if errorlevel 1 exit /b %errorlevel%

rem Exit 0 means the supported AMD ROCm path; exit 1 selects NVIDIA, as before.
call :phase "GPU detection"
call uv run python -m server.gpu_amd >> "%LOG%" 2>&1
set "gpu_result=%errorlevel%"
if "%gpu_result%"=="1" goto ml_nvidia
if not "%gpu_result%"=="0" call :fail "%gpu_result%" "GPU detection"
if not "%gpu_result%"=="0" exit /b %errorlevel%
goto ml_amd

:ml_nvidia
call :phase "NVIDIA ML environment"
cmd /c "set UV_PROJECT_ENVIRONMENT=.venv-ml&& uv sync --frozen --group ml" >> "%LOG%" 2>&1
if errorlevel 1 call :fail "%errorlevel%" "NVIDIA ML environment"
if errorlevel 1 exit /b %errorlevel%

call :phase "skinning environment"
cmd /c "set UV_PROJECT_ENVIRONMENT=.venv-skin&& uv sync --frozen --group skin" >> "%LOG%" 2>&1
if errorlevel 1 call :fail "%errorlevel%" "skinning environment"
if errorlevel 1 exit /b %errorlevel%
goto ml_ready

:ml_amd
call :phase "AMD ROCm environment"
rem AMD RX 9070: install ROCm only when requirements-ml-rocm.txt changed; no .venv-skin on ROCm.
fc /b requirements-ml-rocm.txt .venv-ml\rocm-installed.txt >nul 2>nul && goto ml_ready
echo AMD GPU: installing the ROCm environment; details are in "%LOG%".
if exist .venv-ml\Scripts\python.exe goto amd_venv_ready
call uv venv --python 3.12 .venv-ml >> "%LOG%" 2>&1
if errorlevel 1 call :fail "%errorlevel%" "AMD ROCm environment"
if errorlevel 1 exit /b %errorlevel%
:amd_venv_ready
call uv pip install --python .venv-ml -r requirements-ml-rocm.txt >> "%LOG%" 2>&1
if errorlevel 1 call :fail "%errorlevel%" "AMD ROCm environment"
if errorlevel 1 exit /b %errorlevel%
rem FlexGEMM runs without triton-windows on ROCm; removing it is optional.
call uv pip uninstall --python .venv-ml triton-windows >> "%LOG%" 2>&1
.venv-ml\Scripts\rocm-sdk init >> "%LOG%" 2>&1
if errorlevel 1 call :fail "%errorlevel%" "AMD ROCm environment"
if errorlevel 1 exit /b %errorlevel%
copy /y requirements-ml-rocm.txt .venv-ml\rocm-installed.txt >nul
if errorlevel 1 call :fail "%errorlevel%" "AMD ROCm environment"
if errorlevel 1 exit /b %errorlevel%

:ml_ready
call :phase "Blender installation/check"
call uv run python -m server.blender_install >> "%LOG%" 2>&1
if errorlevel 1 call :fail "%errorlevel%" "Blender installation/check"
if errorlevel 1 exit /b %errorlevel%

call :phase "gltfpack installation/check"
call uv run python -m server.gltfpack_install >> "%LOG%" 2>&1
if errorlevel 1 call :fail "%errorlevel%" "gltfpack installation/check"
if errorlevel 1 exit /b %errorlevel%

call :phase "UI freshness check"
call uv run python tools\ui_stale.py >> "%LOG%" 2>&1
set "stale=%errorlevel%"
if "%stale%"=="0" goto ui_ready
if "%stale%"=="1" goto ui_build
if "%stale%"=="2" goto ui_build
call :fail "%stale%" "UI freshness check"
if errorlevel 1 exit /b %errorlevel%

:ui_build
where npm >nul 2>nul
if errorlevel 1 goto npm_missing
call :phase "UI build"
pushd ui
if errorlevel 1 call :fail "%errorlevel%" "UI build"
if errorlevel 1 exit /b %errorlevel%
if "%stale%"=="2" goto ui_install
goto ui_run_build

:ui_install
call :phase "UI dependency installation"
call npm ci >> "%LOG%" 2>&1
if errorlevel 1 call :fail "%errorlevel%" "UI dependency installation"
if errorlevel 1 exit /b %errorlevel%

:ui_run_build
call :phase "UI build"
call npm run build >> "%LOG%" 2>&1
if errorlevel 1 call :fail "%errorlevel%" "UI build"
if errorlevel 1 exit /b %errorlevel%
popd
if errorlevel 1 call :fail "%errorlevel%" "UI build"
if errorlevel 1 exit /b %errorlevel%
goto ui_ready

:npm_missing
if exist ui\dist\index.html (
  echo The UI source is newer than ui\dist and npm was not found; serving the existing UI.
  >> "%LOG%" echo UI source is newer, but npm was not found; serving existing ui\dist.
  goto ui_ready
)
echo UI build files are missing and npm was not found: install Node.js from https://nodejs.org/.
call :fail 1 "UI dependency check"
exit /b 1

:ui_ready
call :phase "Effigy server startup"
call uv run python -m server
if errorlevel 1 call :fail "%errorlevel%" "Effigy server startup"
if errorlevel 1 exit /b %errorlevel%
>> "%LOG%" echo Effigy server process exited successfully.
exit /b 0

:uv_missing
>> "%LOG%" echo uv was not found on PATH.
echo uv not found: install it from https://docs.astral.sh/uv/
call :fail 1 "uv discovery"
exit /b 1

:phase
set "PHASE=%~1"
echo [Effigy] %~1
>> "%LOG%" echo [phase] %~1
exit /b 0

:fail
set "FAIL_CODE=%~1"
set "FAIL_PHASE=%~2"
if "%FAIL_CODE%"=="" set "FAIL_CODE=1"
>> "%LOG%" echo [failure] Phase: %FAIL_PHASE%
>> "%LOG%" echo [failure] Exit code: %FAIL_CODE%
>> "%LOG%" echo [failure] Effigy could not finish starting.
echo.
echo Effigy could not finish starting.
echo Failed phase: %FAIL_PHASE%
echo Exit code: %FAIL_CODE%
echo Log file: "%LOG%"
echo Read the log before retrying, and attach it to a support request if the problem continues.
if not "%EFFIGY_NO_PAUSE%"=="1" pause
exit /b %FAIL_CODE%

:log_unavailable
echo Effigy could not create its startup log at "%LOG%".
echo Check that the Effigy folder is writable, then start Effigy again.
if not "%EFFIGY_NO_PAUSE%"=="1" pause
exit /b 1
