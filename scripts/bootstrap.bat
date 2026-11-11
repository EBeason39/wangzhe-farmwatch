@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
pushd "%~dp0.." || exit /b 1
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8:replace"
set "PY=venv\Scripts\python.exe"
if exist "%PY%" goto validate
if /i "%~1"=="stop" goto no_environment
if exist "venv\" goto bad_environment
set "BOOT=runtime\Python312\python.exe"
if exist "%BOOT%" goto create
set "BOOT=py -3.12"
%BOOT% -I -c "import struct,sys;sys.exit(not(sys.version_info[:2]==(3,12) and struct.calcsize('P')==8))" >nul 2>&1
if not errorlevel 1 goto create
set "BOOT=python"
%BOOT% -I -c "import struct,sys;sys.exit(not(sys.version_info[:2]==(3,12) and struct.calcsize('P')==8))" >nul 2>&1
if errorlevel 1 goto no_python
:create
%BOOT% -m venv venv
if errorlevel 1 goto failed
:validate
"%PY%" -I -c "import struct,sys;sys.exit(not(sys.version_info[:2]==(3,12) and struct.calcsize('P')==8 and sys.prefix!=sys.base_prefix))" >nul 2>&1
if errorlevel 1 goto bad_environment
if /i "%~1"=="stop" goto run
if /i "%~1"=="status" goto run
if /i "%~1"=="install" goto install
"%PY%" -X utf8 -c "import cv2,numpy,rapidocr_onnxruntime,onnxruntime" >nul 2>&1
if not errorlevel 1 goto config
:install
"%PY%" -I -m pip --require-virtualenv --disable-pip-version-check install --only-binary=:all: -r requirements.txt
if errorlevel 1 goto failed
"%PY%" -I -m pip check
if errorlevel 1 goto failed
:config
if not exist "settings.ini" goto initialize
goto ready
:initialize
"%PY%" -B -X utf8 -m farmwatch init-config
if errorlevel 1 goto failed
:ready
if /i "%~1"=="install" goto selftest
:run
"%PY%" -B -X utf8 -u -m farmwatch %*
set "RC=%errorlevel%"
popd
exit /b %RC%
:selftest
"%PY%" -B -X utf8 -m farmwatch selftest
set "RC=%errorlevel%"
popd
exit /b %RC%
:no_python
echo [ERROR] Python 3.12 x64 was not found. Install Python 3.12, then rerun.
goto failed
:no_environment
echo [INFO] No project Python environment; no FarmWatch process was stopped.
goto failed
:bad_environment
echo [ERROR] The venv is incomplete or is not Python 3.12 x64. Restore the verified environment or rebuild it.
:failed
popd
exit /b 1
