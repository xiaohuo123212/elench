@echo off
REM Elench CLI launcher for Windows (ASCII only, CRLF)
setlocal
set "HERE=%~dp0"
set "SCRIPT=%HERE%..\python\elench.py"
set "PY="
if exist "%HERE%..\runtime\python.exe" set "PY=%HERE%..\runtime\python.exe"
for %%P in (python.exe python3.exe) do (
  if not defined PY (
    where %%P >nul 2>nul && set "PY=%%P"
  )
)
if not defined PY (
  for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if not defined PY if exist "%%D\python.exe" set "PY=%%D\python.exe"
  )
)
if not defined PY (
  where py >nul 2>nul && set "PY=py"
)
if not defined PY (
  echo [elench] Python 3.10+ not found. Install from https://www.python.org/downloads/
  exit /b 2
)
"%PY%" "%SCRIPT%" %*
exit /b %ERRORLEVEL%
