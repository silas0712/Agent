@echo off
rem ============================================================================
rem  agentteam one-click launcher (Windows)
rem
rem  Usage:
rem    scripts\start-agentteam.bat            start the Web UI + open the browser
rem    scripts\start-agentteam.bat cli        run one goal on the command line
rem    scripts\start-agentteam.bat cli --goal "write a fibonacci module + test"
rem    scripts\start-agentteam.bat check      print the resolved configuration
rem    scripts\start-agentteam.bat sessions   list recent session records
rem    scripts\start-agentteam.bat test       run the unit tests
rem    scripts\start-agentteam.bat help       show this help
rem
rem  Environment:
rem    AGENTTEAM_PY           python interpreter to use (default: py -3 / python)
rem    AGENTTEAM_NO_PAUSE=1   do not wait for a key press at the end (for CI)
rem
rem  NOTE: keep this file ASCII-only - cmd.exe parses .bat files with the OEM
rem        code page, so non-ASCII text here would be garbled.  All Chinese
rem        output comes from the Python side, running under code page 65001.
rem ============================================================================
setlocal EnableExtensions
chcp 65001 >nul
title agentteam

set "PUSHED="
set "ROOT=%~dp0.."
pushd "%ROOT%"
if errorlevel 1 (
  echo [ERROR] cannot enter the project directory "%ROOT%"
  goto :fail
)
set "PUSHED=1"

set "MODE=web"
if /i "%~1"=="cli"      goto :mode_cli
if /i "%~1"=="web"      goto :mode_web
if /i "%~1"=="check"    goto :mode_check
if /i "%~1"=="test"     goto :mode_test
if /i "%~1"=="sessions" goto :mode_sessions
if /i "%~1"=="help"     goto :usage
if /i "%~1"=="-h"       goto :usage
if /i "%~1"=="--help"   goto :usage
goto :collect_rest

:mode_cli
set "MODE=cli" & shift
goto :collect_rest

:mode_web
set "MODE=web" & shift
goto :collect_rest

:mode_check
set "MODE=check" & shift
goto :collect_rest

:mode_test
set "MODE=test" & shift
goto :collect_rest

:mode_sessions
set "MODE=sessions" & shift
goto :collect_rest

:collect_rest
rem Quoted arguments are kept as-is, so --goal "a b" survives the round trip.
set "REST="
:rest_loop
if "%~1"=="" goto :find_python
set "REST=%REST% %1"
shift
goto :rest_loop

:find_python
set "PYTHON=%AGENTTEAM_PY%"
if defined PYTHON goto :check_python
where py >nul 2>nul
if not errorlevel 1 set "PYTHON=py -3"
if not defined PYTHON (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON=python"
)
if not defined PYTHON goto :no_python

:check_python
%PYTHON% -c "import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python 3.10+ is required. Current interpreter: %PYTHON%
  %PYTHON% --version
  goto :fail
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] creating the virtual environment .venv ...
  %PYTHON% -m venv ".venv"
  if errorlevel 1 goto :fail
) else (
  echo [1/3] reusing the existing .venv
)
set "PY=.venv\Scripts\python.exe"

"%PY%" -c "import fastapi, uvicorn, websockets, rich, dotenv, pydantic" >nul 2>nul
if errorlevel 1 (
  echo [2/3] installing dependencies ^(the first run takes a few minutes^) ...
  "%PY%" -m pip install --upgrade pip --quiet
  if errorlevel 1 goto :fail
  "%PY%" -m pip install -e ".[web]"
  if errorlevel 1 goto :fail
) else (
  echo [2/3] dependencies are ready
)

echo [3/3] mode: %MODE%%REST%
echo.

if /i "%MODE%"=="cli"      goto :run_cli
if /i "%MODE%"=="check"    goto :run_check
if /i "%MODE%"=="test"     goto :run_test
if /i "%MODE%"=="sessions" goto :run_sessions
goto :run_web

:run_cli
"%PY%" -m agentteam.main %REST%
goto :done

:run_check
"%PY%" -m agentteam.main --check %REST%
goto :done

:run_test
"%PY%" -m pytest %REST%
goto :done

:run_sessions
"%PY%" -m agentteam.main --list-sessions %REST%
goto :done

:run_web
echo Web UI: http://127.0.0.1:8765  (press Ctrl+C to stop)
"%PY%" -m agentteam.main --serve --open-browser %REST%
goto :done

:done
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo [ERROR] exit code %CODE%
)
if defined PUSHED popd
if "%AGENTTEAM_NO_PAUSE%"=="1" exit /b %CODE%
echo.
echo Press any key to close this window ...
pause >nul
exit /b %CODE%

:usage
echo agentteam launcher - usage:
echo   start-agentteam.bat [web^|cli^|check^|sessions^|test^|help] [extra args...]
echo.
echo   web       start the Web UI (default, opens the browser)
echo   cli       run the built-in demo goal on the command line
echo   check     print the resolved configuration and exit
echo   sessions  list the recent session records
echo   test      run the unit tests (pytest)
echo   help      show this help
echo.
echo Examples:
echo   start-agentteam.bat
echo   start-agentteam.bat cli --goal "write a fibonacci module with tests"
echo   start-agentteam.bat web --port 9000
echo   start-agentteam.bat sessions --list-sessions 10
echo.
echo See "agentteam --help" for every option (Chinese help text).
if defined PUSHED popd
exit /b 0

:no_python
echo [ERROR] Python was not found.
echo         Install Python 3.10+ from https://www.python.org/downloads/
echo         or point AGENTTEAM_PY at a python.exe.
goto :fail

:fail
if defined PUSHED popd
echo.
echo Startup failed. Press any key to close this window ...
pause >nul
exit /b 1
