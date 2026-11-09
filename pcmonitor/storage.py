"""Логирование показателей в CSV или JSON Lines и чтение логов обратно."""

from __future__ import annotations

import csv
import json
from pathlib import Path

# Плоские поля CSV-лога (одна строка = один замер)
CSV_FIELDS = [
    "timestamp", "cpu_percent", "cpu_freq_mhz", "ram_percent", "ram_used_gb", "swap_percent",
    "temp_max", "disk_min_free_percent", "disk_min_free_mount", "smart_status", "uptime_seconds", "alerts",
]


def flatten(snapshot: dict, alerts: list) -> dict:
    """Превратить вложенный снимок в плоскую запись для CSV."""
    disks = [d for d in snapshot.get("disks", []) if "percent" in d]
    worst = min(disks, key=lambda d: 100 - d["percent"], default=None)
    drives = snapshot.get("smart", {}).get("drives", [])
    statuses = {d.get("health") for d in drives if d.get("health")}
    smart_status = "FAIL" if "FAIL" in statuses else "WARNING" if "WARNING" in statuses else (
        "OK" if statuses else "N/A")
    return {
        "timestamp": snapshot.get("timestamp"),
        "cpu_percent": snapshot.get("cpu", {}).get("percent"),
        "cpu_freq_mhz": snapshot.get("cpu", {}).get("freq_mhz"),
        "ram_percent": snapshot.get("memory", {}).get("percent"),
        "ram_used_gb": snapshot.get("memory", {}).get("used_gb"),
        "swap_percent": snapshot.get("memory", {}).get("swap_percent"),
        "temp_max": snapshot.get("temperature", {}).get("max"),
        "disk_min_free_percent": round(100 - worst["percent"], 1) if worst else None,
        "disk_min_free_mount": worst["mount"] if worst else None,
        "smart_status": smart_status,
        "uptime_seconds": snapshot.get("uptime", {}).get("seconds"),
        "alerts": " | ".join(str(a) for a in alerts),
    }


def excel_row(row: dict) -> dict:
    """Дробные числа — с запятой: так Excel с русскими настройками не превращает 21.6 в «21.июн»."""
    return {k: (str(v).replace(".", ",") if isinstance(v, float) else v) for k, v in row.items()}


class MetricsLogger:
    """Дописывает замеры в файл. Формат определяется расширением: .csv или .json/.jsonl."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.fmt = "csv" if self.path.suffix.lower() == ".csv" else "json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, snapshot: dict, alerts: list) -> None:
        if self.fmt == "csv":
            new_file = not self.path.exists() or self.path.stat().st_size == 0
            # utf-8-sig — чтобы Excel корректно открыл кириллицу
            with self.path.open("a", newline="", encoding="utf-8-sig" if new_file else "utf-8") as f:
                w = csv.DictWriter(f, fieldnames=CSV_FIELDS, delimiter=";")
                if new_file:
                    w.writeheader()
                w.writerow(excel_row(flatten(snapshot, alerts)))
        else:
            record = dict(snapshot, alerts=[a.__dict__ for a in alerts])
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _num(v):
    if v in (None, "", "None"):
        return None
    try:
        return float(str(v).replace(",", "."))   # в логе может быть и 21.6, и 21,6
    except (TypeError, ValueError):
        return None


def read_log(path: str | Path) -> list[dict]:
    """Прочитать лог (CSV или JSON Lines) в список плоских записей."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Файл лога не найден: {path}")
    rows: list[dict] = []
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f, delimiter=";"):
                rows.append(r)
    else:
        from .thresholds import Alert
        with path.open(encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # битая строка (например, прерванная запись) — пропускаем
                alerts = [Alert(**a) for a in rec.get("alerts", [])]
                rows.append(flatten(rec, alerts))
    for r in rows:
        for k in ("cpu_percent", "cpu_freq_mhz", "ram_percent", "ram_used_gb", "swap_percent",
                  "temp_max", "disk_min_free_percent", "uptime_seconds"):
            r[k] = _num(r.get(k))
    return rows
