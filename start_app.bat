@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

title Mailbox Detection Studio
echo.
echo ============================================================
echo   Mailbox Detection Studio
echo   Backend agents 1-5  +  Frontend UI
echo ============================================================
echo.

if not exist ".env" (
  if exist ".env.example" (
    copy /Y ".env.example" ".env" >nul
    echo Created .env from example - add GOOGLE_MAPS_API_KEY for live Street View.
  )
)

REM ---- Port: environment override, then .env, then default ----
if not defined PORT (
  if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
      if /I "%%A"=="PORT" set "PORT=%%B"
    )
  )
)
if not defined PORT set "PORT=8002"

REM Prefer the tested Python 3.12 runtime, then fall back.
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3.12 --version >nul 2>&1
  if !ERRORLEVEL!==0 (
    set "PY=py -3.12"
  ) else (
    set "PY=py -3"
  )
) else (
  set "PY=python"
)

echo [1/6] Checking Python...
%PY% --version
if errorlevel 1 (
  echo ERROR: Python not found. Install Python 3.10+ and try again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv --system-site-packages ".venv"
  if errorlevel 1 (
    echo ERROR: Could not create the project Python environment.
    pause
    exit /b 1
  )
)
set "PY=%~dp0.venv\Scripts\python.exe"
set "PY="%PY%""

REM Keep one stable backend. Multiple ports create separate in-memory workers
REM and make Force Stop target the wrong process.
netstat -ano | findstr /C:":%PORT% " | findstr /C:"LISTENING" >nul
if not errorlevel 1 (
  echo Mailbox Detection Studio is already running on port %PORT%.
  start "" "http://localhost:%PORT%"
  exit /b 0
)

echo.
echo [2/6] Starting PostgreSQL and Redis cache containers...
where docker >nul 2>&1
if errorlevel 1 (
  echo WARNING: Docker was not found. The app will run, but API caching will be unavailable.
) else (
  docker compose up -d postgres redis
  if errorlevel 1 (
    echo WARNING: Cache containers did not start. The app will fall back to live APIs.
  ) else (
    echo Cache services started.
  )
)

echo.
echo [3/6] Checking backend dependencies...
%PY% -c "import fastapi,uvicorn,geopandas,shapely,PIL,cv2,redis,psycopg,torch,spandrel; from rfdetr import RFDETRLarge; import importlib.metadata; assert importlib.metadata.version('rfdetr') == '1.10.1'" >nul 2>&1
if errorlevel 1 (
  echo Missing dependencies - installing them now...
  %PY% -m pip install -r "backend\requirements.txt" -q
  if errorlevel 1 (
    echo ERROR: Backend dependencies could not be installed. The pipeline cannot run.
    pause
    exit /b 1
  )
) else (
  echo Backend dependencies are ready.
)

%PY% -c "from dotenv import load_dotenv; load_dotenv('.env'); import os,torch; required=os.environ.get('MAILBOX_ENHANCEMENT_DEVICE','auto')=='cuda'; available=torch.cuda.is_available(); print('PyTorch:',torch.__version__,'- GPU:',torch.cuda.get_device_name(0) if available else 'unavailable'); assert not required or available, 'GPU enhancement requires CUDA PyTorch. Install backend\requirements-cuda.txt into .venv and restart.'"
if errorlevel 1 (
  echo ERROR: GPU enhancement is configured but CUDA is not available.
  pause
  exit /b 1
)

echo.
echo [4/6] Detecting network address...
set "LAN_IP="
for /f "usebackq delims=" %%I in (`.venv\Scripts\python.exe -c "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.connect(('8.8.8.8',80));print(s.getsockname()[0]);s.close()"`) do set "LAN_IP=%%I"
if not defined LAN_IP set "LAN_IP=127.0.0.1"

set "URL_LOCAL=http://localhost:%PORT%"
set "URL_NETWORK=http://%LAN_IP%:%PORT%"

echo.
echo ============================================================
echo   Access URLs  (dynamic for this machine)
echo ============================================================
echo   This PC          : %URL_LOCAL%
echo   Other devices    : %URL_NETWORK%
echo   Same Wi-Fi / LAN required for other phones/PCs.
echo   If blocked, allow Python/port %PORT% in Windows Firewall.
echo ============================================================
echo.
echo [5/6] Starting server on 0.0.0.0:%PORT% (all network interfaces)...
echo      Pipeline: Agent1 -^> Agent2 -^> Agent3 -^> Agent4 -^> Agent5
echo      Press Ctrl+C in this window to stop.
echo.

REM Open local browser; other systems use the Network URL above
start "" cmd /c "timeout /t 3 /nobreak >nul && start %URL_LOCAL%"

echo [6/6] Server running...
cd /d "%~dp0backend"
set "PORT=%PORT%"
set "BIND_HOST=0.0.0.0"
%PY% server.py
set "ERR=%ERRORLEVEL%"
cd /d "%~dp0"

echo.
if not "%ERR%"=="0" (
  echo Server exited with code %ERR%.
) else (
  echo Server stopped.
)
pause
endlocal
