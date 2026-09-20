@echo off
REM InfraDeck-managed restart for model-gateway (deployment_id: model-gateway-laptop)
REM
REM Canonical path: InfraDeck resolves deployment_id=model-gateway-laptop -> recovery_action_id ->
REM ra-model-gateway-laptop -> target=this file. No caller-supplied arguments are used.
REM This script is read-only from InfraDeck's perspective; InfraDeck only runs it, never reads it.

setlocal EnableExtensions EnableDelayedExpansion
set "ROOT=C:\projects\model-gateway"
set "PYTHON=C:\projects\model-gateway\.venv\Scripts\python.exe"
set "HEALTH_URL=http://127.0.0.1:8700/health"

REM === Step 1: Find and kill the process holding port 8700 ===
REM netstat output may contain multiple lines per port (LISTENING + TIME_WAIT).
REM Use /C:"LISTENING" to match only the LISTENING state line.
REM Line format: TCP  127.0.0.1:8700  0.0.0.0:0  LISTENING  <PID>
REM tokens: [0]=TCP [1]=local [2]=remote [3]=LISTENING [4]=PID
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /C:":8700" ^| findstr /C:"LISTENING"') do (
    echo [restart-model-gateway] killing PID %%P on port 8700
    taskkill /F /PID %%P >nul 2>&1
)

REM === Step 2: Wait up to 10s for port 8700 to be released ===
set "WAIT=0"
:wait_port
netstat -ano | findstr /C:":8700" | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    if !WAIT! LSS 10 (
        set /a WAIT+=1
        ping -n 2 127.0.0.1 >nul
        goto :wait_port
    )
    echo [restart-model-gateway] FAIL: port 8700 still LISTENING after 10s
    exit /b 2
)
echo [restart-model-gateway] port 8700 released

REM === Step 3: Relaunch the gateway via a detached Python launcher ===
REM CRITICAL: the long-lived gateway MUST NOT inherit the parent cmd.exe's
REM stdin/stdout/stderr handles. If it does, the Python RecoveryExecutor's
REM subprocess.run(..., capture_output=True) pipe stays open and the executor
REM never returns — UI is stuck on "Running", post-diagnostic and recovery
REM record are never written.
REM
REM We use a dedicated Python launcher (restart-gateway-launcher.py) that
REM spawns the gateway with:
REM   - stdin=None, stdout=None, stderr=None  (no inherited pipes)
REM   - close_fds=True                         (close all other inherited FDs)
REM   - creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
REM     (gateway gets no console handle from recovery cmd.exe)
REM The launcher exits in ~0.5s after spawning. The gateway then runs with
REM no shared handles with the recovery process, so the recovery pipe gets
REM EOF immediately and the RecoveryExecutor returns.
"%PYTHON%" "%ROOT%\restart-gateway-launcher.py"

REM === Step 4: Poll /health until 200 (max 30s) ===
set "ATTEMPTS=0"
:wait_health
for /f %%A in ('curl -s -m 2 -o nul -w "%%{http_code}" "%HEALTH_URL%"') do set "HTTP_CODE=%%A"
if "!HTTP_CODE!"=="200" (
    echo [restart-model-gateway] /health OK, restart complete
    exit /b 0
)
if !ATTEMPTS! LSS 30 (
    set /a ATTEMPTS+=1
    ping -n 2 127.0.0.1 >nul
    goto :wait_health
)
echo [restart-model-gateway] FAIL: /health never returned 200 after 30s
exit /b 1
