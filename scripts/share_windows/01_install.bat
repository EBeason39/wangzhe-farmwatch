@echo off
setlocal
call "%~dp0scripts\bootstrap.bat" install %*
set "RC=%errorlevel%"
pause
exit /b %RC%
