@echo off
REM Double-click this file to launch HomerunIQ. Requires only Docker Desktop.
cd /d "%~dp0"
echo Starting HomerunIQ...

where docker >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo Docker was not found. Install Docker Desktop first:
    echo https://www.docker.com/products/docker-desktop/
    pause
    exit /b 1
)

docker info >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo Docker is installed but not running.
    echo Open Docker Desktop, wait for "Engine running", then re-run this script.
    pause
    exit /b 1
)

if not exist backend\.env copy backend\.env.example backend\.env >nul
if not exist frontend\.env.local copy frontend\.env.example frontend\.env.local >nul

echo Building and starting containers (first run takes a few minutes)...
docker compose up --build -d

echo Waiting for the app to come online...
timeout /t 30 /nobreak >nul

echo.
echo HomerunIQ is running!
echo    Dashboard: http://localhost:3000
echo    API docs:  http://localhost:8000/docs
echo.
echo Note: first page load pulls live rosters and Statcast data (10-30 sec).
echo.
start http://localhost:3000
echo To stop later, double-click stop.bat
pause
