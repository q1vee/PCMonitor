"""Проверка пороговых значений."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

WARNING = "WARNING"
CRITICAL = "CRITICAL"


@dataclass
class Thresholds:
    cpu_percent: float = 90.0          # загрузка CPU, %
    ram_percent: float = 90.0          # занятость ОЗУ, %
    temp_warning: float = 85.0         # температура — предупреждение, °C
    temp_critical: float = 95.0        # температура — критично, °C
    disk_free_percent: float = 10.0    # свободно на разделе меньше, %
    drive_temp: float = 55.0           # температура накопителя, °C
    reallocated_sectors: int = 0       # переназначенные сектора больше

    @classmethod
    def load(cls, path: str | Path | None) -> "Thresholds":
        """Загрузить пороги из JSON-файла; неизвестные ключи игнорируются."""
        if not path:
            return cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Alert:
    level: str
    metric: str
    message: str
    value: float | int | str | None = field(default=None)

    def __str__(self) -> str:
        return f"[{self.level}] {self.message}"


def check(snapshot: dict, t: Thresholds) -> list[Alert]:
    """Сравнить снимок с порогами и вернуть список предупреждений."""
    alerts: list[Alert] = []

    cpu = snapshot.get("cpu", {}).get("percent")
    if cpu is not None and cpu > t.cpu_percent:
        alerts.append(Alert(WARNING, "cpu", f"Загрузка CPU {cpu}% > {t.cpu_percent}%", cpu))

    ram = snapshot.get("memory", {}).get("percent")
    if ram is not None and ram > t.ram_percent:
        alerts.append(Alert(WARNING, "ram", f"Занято ОЗУ {ram}% > {t.ram_percent}%", ram))

    for s in snapshot.get("temperature", {}).get("sensors", []):
        c = s.get("celsius")
        if c is None:
            continue
        if c > t.temp_critical:
            alerts.append(Alert(CRITICAL, "temperature", f"Температура {s['name']}: {c} °C > {t.temp_critical} °C", c))
        elif c > t.temp_warning:
            alerts.append(Alert(WARNING, "temperature", f"Температура {s['name']}: {c} °C > {t.temp_warning} °C", c))

    for d in snapshot.get("disks", []):
        if "percent" not in d:
            continue
        free_pct = round(100 - d["percent"], 1)
        if free_pct < t.disk_free_percent:
            alerts.append(Alert(WARNING, "disk", f"Диск {d['mount']}: свободно {free_pct}% < {t.disk_free_percent}%", free_pct))

    for drv in snapshot.get("smart", {}).get("drives", []):
        name = drv.get("model") or drv.get("device")
        health = drv.get("health")
        if health == "FAIL":
            alerts.append(Alert(CRITICAL, "smart", f"Накопитель {name}: SMART — неисправен", health))
        elif health == "WARNING":
            alerts.append(Alert(WARNING, "smart", f"Накопитель {name}: SMART — предупреждение", health))
        realloc = drv.get("reallocated_sectors")
        if isinstance(realloc, int) and realloc > t.reallocated_sectors:
            alerts.append(Alert(WARNING, "smart", f"Накопитель {name}: переназначенных секторов {realloc}", realloc))
        temp = drv.get("temperature")
        if isinstance(temp, (int, float)) and temp > t.drive_temp:
            alerts.append(Alert(WARNING, "smart", f"Накопитель {name}: температура {temp} °C > {t.drive_temp} °C", temp))

    return alerts
