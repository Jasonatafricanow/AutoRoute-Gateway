@echo off
REM Inner launcher: starts the long-lived gateway in a NEW console process
REM and exits immediately. This breaks the stdio handle chain:
REM   RecoveryExecutor (Python pipe)
REM   -> restart-model-gateway.bat (cmd.exe)
REM   -> start_inner.bat (cmd.exe, exits fast)
REM   -> gateway python.exe (no inherited pipe, attached to its own console)
REM
REM Without this indirection, the gateway inherits the cmd.exe's stdio
REM handles, and the RecoveryExecutor's subprocess.run(..., capture_output=True)
REM never sees EOF on its pipe.

setlocal
set "ROOT=C:\projects\model-gateway"
set "PYTHON=C:\projects\model-gateway\.venv\Scripts\python.exe"
set "LOG=%ROOT%\gateway.log"
cd /d "%ROOT%"
"%PYTHON%" -m tools.run_gateway --config gateway.yaml > "%LOG%" 2>&1
