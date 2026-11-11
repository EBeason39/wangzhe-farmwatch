@echo off
setlocal
call "%~dp0scripts\bootstrap.bat" run %*
set "RC=%errorlevel%"
pause
exit /b %RC%
