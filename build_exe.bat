@echo off
rem Build standalone PCMonitor.exe (no Python needed on target PCs).
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --console --name PCMonitor --distpath dist --workpath build --specpath build monitor.py
echo.
echo Done: dist\PCMonitor.exe
pause
