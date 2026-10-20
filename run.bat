@echo off
rem Start Effigy: sync the backend env, build the UI when its source changed, serve on 127.0.0.1 (port 8000
rem unless chosen in Setup or set in EFFIGY_PORT; python -m server opens the browser there).
cd /d "%~dp0"
rem portable zip (tools/package.py): the bundled uv; Python, uv cache and envs stay in this folder
if exist uv.exe (
  set "PATH=%~dp0;%PATH%"
  set "UV_PYTHON_INSTALL_DIR=%~dp0python"
  set "UV_CACHE_DIR=%~dp0cache"
  set "UV_PYTHON_PREFERENCE=only-managed"
)
where uv >nul 2>nul || (echo uv not found: install it from https://docs.astral.sh/uv/ & exit /b 1)
uv sync --frozen || exit /b 1
rem the ML venv for steps, for the GPU this machine has; run.log says which, and holds the AMD install's output
set "LOG=%~dp0run.log"
> "%LOG%" echo run.bat %date% %time%
uv run python -m server.gpu_amd >> "%LOG%" 2>&1 && goto ml_amd
rem NVIDIA: torch cu128 + our wheels from uv.lock; a no-op once synced
cmd /c "set UV_PROJECT_ENVIRONMENT=.venv-ml&& uv sync --frozen --group ml" || exit /b 1
rem the skinning venv (M20, SkinTokens): its own because of a GPL-provenance subtree; a no-op once synced
cmd /c "set UV_PROJECT_ENVIRONMENT=.venv-skin&& uv sync --frozen --group skin" || exit /b 1
goto ml_ready
:ml_amd
rem AMD RX 9070: torch ROCm + wheels\rocm (docs/howto/amd-rocm-windows.md), installed again only when
rem requirements-ml-rocm.txt changed (.venv-ml keeps a copy). No .venv-skin: the character chain isn't on ROCm yet
fc /b requirements-ml-rocm.txt .venv-ml\rocm-installed.txt >nul 2>nul && goto ml_ready
echo AMD GPU: installing the ROCm environment (several GB the first time), log in %LOG%
if not exist .venv-ml\Scripts\python.exe (uv venv --python 3.12 .venv-ml >> "%LOG%" 2>&1 || goto ml_amd_failed)
uv pip install --python .venv-ml -r requirements-ml-rocm.txt >> "%LOG%" 2>&1 || goto ml_amd_failed
rem flex_gemm's metadata pulls triton-windows in; FlexGEMM runs without it on ROCm (not there: fine)
uv pip uninstall --python .venv-ml triton-windows >> "%LOG%" 2>&1
.venv-ml\Scripts\rocm-sdk init >> "%LOG%" 2>&1 || goto ml_amd_failed
copy /y requirements-ml-rocm.txt .venv-ml\rocm-installed.txt >nul
goto ml_ready
:ml_amd_failed
echo The AMD ROCm environment did not install: the details are in %LOG%
exit /b 1
:ml_ready
rem pinned Blender 5.2.2, downloaded and SHA-256 checked on first run
uv run python -m server.blender_install || exit /b 1
uv run python -m server.gltfpack_install || exit /b 1
rem the UI: built when missing or older than its source (a pull), npm ci first when its lock changed
uv run python tools\ui_stale.py
set "stale=%errorlevel%"
if "%stale%"=="0" goto ui_ready
where npm >nul 2>nul && goto ui_build
if exist ui\dist\index.html (
  echo ui\dist is older than ui\src and npm was not found: serving the old UI
  goto ui_ready
)
echo ui\dist missing and npm not found: install Node 22 from https://nodejs.org/ & exit /b 1
:ui_build
pushd ui
if "%stale%"=="2" (call npm ci || exit /b 1)
call npm run build || exit /b 1
popd
:ui_ready
uv run python -m server
