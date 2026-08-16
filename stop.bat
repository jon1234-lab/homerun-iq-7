@echo off
cd /d "%~dp0"
echo Stopping HomerunIQ...
docker compose down
echo Stopped. Double-click start.bat to launch again.
pause
