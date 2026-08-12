@echo off
setlocal
py -3 "%~dp0notifierd.py" %*
exit /b %ERRORLEVEL%
