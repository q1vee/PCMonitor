@echo off
rem Run PC Monitor menu. For CPU temperature: right click - "Run as administrator".
chcp 65001 >nul
cd /d "%~dp0"
python -c "import psutil" 2>nul || python -m pip install -r requirements.txt
python monitor.py
pause
