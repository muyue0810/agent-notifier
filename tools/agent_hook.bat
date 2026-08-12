@echo off
setlocal
py -3 "%~dp0agent_hook.py" %*
exit /b %ERRORLEVEL%
