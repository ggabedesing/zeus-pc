@echo off
setlocal
cd /d "%~dp0"
set PYTHONPATH=%CD%\src
echo.
echo ================================
echo            ZEUS PC
echo ================================
echo Abrindo http://127.0.0.1:8000
start "" http://127.0.0.1:8000
python -m zeus_pc.server
endlocal
