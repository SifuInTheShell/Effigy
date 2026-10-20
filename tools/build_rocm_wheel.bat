@echo off
rem usage: set SP=<build dir> then build.bat <source dir> <log name>
rem SP holds venv-rocm (torch rocm7.2.1 + rocm_sdk_devel, `rocm-sdk init` done) and gets dist\ and logs\
if "%SP%"=="" (echo set SP to the build folder first & exit /b 1)
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
set BUILD_TARGET=rocm
set GPU_ARCHS=gfx1201
set PYTORCH_ROCM_ARCH=gfx1201
set DISTUTILS_USE_SDK=1
set ROCM_HOME=%SP%\venv-rocm\Lib\site-packages\_rocm_sdk_devel
set HIP_PATH=%ROCM_HOME%
set PATH=%ROCM_HOME%\bin;%PATH%
set CXX=%ROCM_HOME%\lib\llvm\bin\clang-cl.exe
set MAX_JOBS=16
"%SP%\venv-rocm\Scripts\python.exe" -m pip wheel --no-build-isolation --no-deps -v -w "%SP%\dist" "%~1" > "%SP%\logs\%~2.log" 2>&1
echo exit=%ERRORLEVEL%
