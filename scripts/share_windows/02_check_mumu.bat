@echo off
setlocal
call "%~dp0scripts\bootstrap.bat" check %*
set "RC=%errorlevel%"
pause
exit /b %RC%
