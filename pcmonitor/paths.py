"""Расположение данных программы на любом компьютере.

- Запуск из исходников: папка проекта.
- Запуск собранного PCMonitor.exe: папка, где лежит exe.
- Если в эту папку нельзя писать (например, Program Files):
  %LOCALAPPDATA%\\PCMonitor в Windows или ~/.pcmonitor в Linux/macOS.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _is_writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):          # PyInstaller: рядом с exe
        base = Path(sys.executable).resolve().parent
    else:                                      # исходники: корень проекта
        base = Path(__file__).resolve().parents[1]
    if _is_writable(base):
        return base
    if os.name == "nt":
        fallback = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "PCMonitor"
    else:
        fallback = Path.home() / ".pcmonitor"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


APP_DIR = _app_dir()
DATA_DIR = APP_DIR / "data"
LOGS_DIR = APP_DIR / "logs"
REPORTS_DIR = APP_DIR / "reports"
MONITORING_REPORTS_DIR = REPORTS_DIR / "monitoring"
HISTORY_FILE = DATA_DIR / "history.jsonl"
