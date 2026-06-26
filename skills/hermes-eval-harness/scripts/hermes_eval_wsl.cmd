@echo off
rem hermes_eval_wsl.cmd — run hermes_eval.py inside WSL where Hermes packages live.
rem
rem Usage:
rem   hermes_eval_wsl.cmd --suite suites/smoke.yaml --backend library --workers 6
rem   hermes_eval_wsl.cmd --suite suites/smoke.yaml --backend api --base-url http://localhost:8642/v1
rem
rem All hermes_eval.py flags pass through unchanged. The library backend works here
rem because WSL Python has access to the Hermes install; it does NOT work when you
rem run hermes_eval.py directly from Windows Python.
rem
rem Environment variables (set before calling, e.g. `set HERMES_WSL_DISTRO=openclaw`):
rem   HERMES_WSL_DISTRO       Target a non-default WSL distro (e.g. openclaw, Ubuntu).
rem   HERMES_WSL_PYTHON       Interpreter inside WSL. Default `python3`. Set this to the
rem                           Hermes venv python for the library backend, e.g.
rem                           /home/openclaw/.hermes/hermes-agent/venv/bin/python3
rem                           (system python3 usually lacks the Hermes packages).
rem   HERMES_WSL_HERMES_HOME  Forwarded into WSL as HERMES_HOME so the library backend
rem                           loads the DEPLOYED config, e.g. /home/openclaw/.hermes
rem                           (the non-interactive WSL shell often has HERMES_HOME unset).

setlocal

set "SCRIPT_DIR=%~dp0"

rem Interpreter inside WSL. Override with HERMES_WSL_PYTHON for venv-based installs.
if not defined HERMES_WSL_PYTHON set "HERMES_WSL_PYTHON=python3"

rem Convert the Windows path of hermes_eval.py to a WSL (POSIX) path.
for /f "delims=" %%i in ('wsl wslpath "%SCRIPT_DIR%hermes_eval.py"') do set "WSL_SCRIPT=%%i"

if "%WSL_SCRIPT%"=="" (
    echo ERROR: wslpath conversion failed. Is WSL installed and a distro available?
    exit /b 1
)

rem Optional distro selector.
set "DISTRO_ARG="
if defined HERMES_WSL_DISTRO set "DISTRO_ARG=-d %HERMES_WSL_DISTRO%"

rem Optional HERMES_HOME forwarding (uses the WSL `env` command so it reaches the
rem interpreter process; the Windows-side HERMES_HOME does not cross into WSL).
set "ENV_PREFIX="
if defined HERMES_WSL_HERMES_HOME set "ENV_PREFIX=env HERMES_HOME=%HERMES_WSL_HERMES_HOME%"

wsl %DISTRO_ARG% %ENV_PREFIX% "%HERMES_WSL_PYTHON%" "%WSL_SCRIPT%" %*
