@echo off
setlocal
call "%~dp0scripts\bootstrap.bat" start %*
set "RC=%errorlevel%"
if not "%RC%"=="0" pause
exit /b %RC%
