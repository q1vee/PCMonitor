"""Сбор показателей системы.

Каждый сборщик возвращает словарь и никогда не бросает исключение наружу:
при ошибке в результат попадает поле ``error`` с описанием, чтобы сбой одного
источника (например, нет прав на чтение SMART) не останавливал мониторинг.
"""

from __future__ import annotations

import json
import logging
import platform
import shutil
import subprocess
import time
from datetime import datetime
from typing import Any

import psutil

log = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"


# --------------------------------------------------------------------------- #
# Вспомогательные функции
# --------------------------------------------------------------------------- #
def _run_powershell(script: str, timeout: int = 20) -> Any:
    """Выполнить PowerShell-скрипт и вернуть разобранный JSON (или None)."""
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
           f"$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; "
           f"{script} | ConvertTo-Json -Depth 4 -Compress"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("PowerShell недоступен: %s", exc)
        return None
    if res.returncode != 0 or not res.stdout.strip():
        log.debug("PowerShell вернул ошибку: %s", res.stderr.strip()[:300])
        return None
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else [data]


def _gb(value: float) -> float:
    return round(value / 1024 ** 3, 2)


# --------------------------------------------------------------------------- #
# CPU, ОЗУ, uptime
# --------------------------------------------------------------------------- #
def collect_cpu(interval: float = 1.0) -> dict:
    try:
        per_core = psutil.cpu_percent(interval=interval, percpu=True)
        freq = psutil.cpu_freq()
        return {
            "percent": round(sum(per_core) / len(per_core), 1) if per_core else 0.0,
            "per_core": per_core,
            "cores_physical": psutil.cpu_count(logical=False),
            "cores_logical": psutil.cpu_count(logical=True),
            "freq_mhz": round(freq.current) if freq else None,
            "model": platform.processor() or None,
        }
    except Exception as exc:  # noqa: BLE001 — сборщик не должен падать
        return {"error": str(exc)}


def collect_memory() -> dict:
    try:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        return {
            "percent": vm.percent,
            "total_gb": _gb(vm.total),
            "used_gb": _gb(vm.total - vm.available),
            "available_gb": _gb(vm.available),
            "swap_percent": sw.percent,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def collect_uptime() -> dict:
    try:
        boot = psutil.boot_time()
        seconds = int(time.time() - boot)
        d, rem = divmod(seconds, 86400)
        h, rem = divmod(rem, 3600)
        m, _ = divmod(rem, 60)
        return {
            "seconds": seconds,
            "human": f"{d} д {h} ч {m} мин",
            "boot_time": datetime.fromtimestamp(boot).isoformat(timespec="seconds"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# --------------------------------------------------------------------------- #
# Диски
# --------------------------------------------------------------------------- #
def collect_disks() -> list[dict]:
    disks = []
    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception as exc:  # noqa: BLE001
        return [{"error": str(exc)}]
    for part in partitions:
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError) as exc:
            disks.append({"mount": part.mountpoint, "error": str(exc)})
            continue
        disks.append({
            "mount": part.mountpoint,
            "fstype": part.fstype,
            "total_gb": _gb(u.total),
            "used_gb": _gb(u.used),
            "free_gb": _gb(u.free),
            "percent": u.percent,
        })
    return disks


# --------------------------------------------------------------------------- #
# Температура
# --------------------------------------------------------------------------- #
def _kind(text: str) -> str:
    """Определить тип датчика по имени/идентификатору: cpu, gpu или other."""
    t = text.lower()
    if any(k in t for k in ("gpu", "nvidia", "radeon", "amdgpu", "nouveau")):
        return "gpu"
    if any(k in t for k in ("cpu", "core", "package", "tctl", "tdie", "k10temp", "coretemp", "zenpower")):
        return "cpu"
    return "other"


def _gpu_temps_nvidia() -> list[dict]:
    """Температура видеокарт NVIDIA через nvidia-smi (работает без прав администратора)."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        res = subprocess.run([exe, "--query-gpu=name,temperature.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("nvidia-smi: %s", exc)
        return []
    out = []
    for line in res.stdout.splitlines():
        name, _, temp = line.rpartition(",")
        try:
            out.append({"name": name.strip() or "NVIDIA GPU", "celsius": float(temp), "kind": "gpu"})
        except ValueError:
            continue
    return out


def collect_temperatures() -> dict:
    """Температуры из доступных источников.

    Порядок: psutil (Linux) → LibreHardwareMonitor/OpenHardwareMonitor (WMI)
    → ACPI thermal zone (WMI, требует прав администратора); видеокарты NVIDIA —
    дополнительно через nvidia-smi. Каждый датчик помечается типом cpu/gpu/other.
    """
    sensors: list[dict] = []
    sources: list[str] = []

    # 1. psutil — работает в Linux/FreeBSD
    if hasattr(psutil, "sensors_temperatures"):
        try:
            for chip, entries in (psutil.sensors_temperatures() or {}).items():
                for e in entries:
                    name = f"{chip} {e.label or ''}".strip()
                    sensors.append({"name": name, "celsius": round(e.current, 1), "kind": _kind(name)})
            if sensors:
                sources.append("psutil")
        except Exception as exc:  # noqa: BLE001
            log.debug("psutil temps: %s", exc)

    # 2. LibreHardwareMonitor / OpenHardwareMonitor (если запущены)
    if not sensors and IS_WINDOWS:
        for ns in ("root/LibreHardwareMonitor", "root/OpenHardwareMonitor"):
            data = _run_powershell(
                f"Get-CimInstance -Namespace {ns} -ClassName Sensor -Filter \"SensorType='Temperature'\" "
                f"| Select-Object Name, Value, Identifier")
            if data:
                for d in data:
                    ident = f"{d.get('Identifier', '')} {d.get('Name', '')}"
                    sensors.append({"name": d.get("Name"), "celsius": round(float(d.get("Value") or 0), 1),
                                    "kind": _kind(ident)})
                sources.append(ns.split("/")[1])
                break

    # 3. ACPI thermal zone — датчик системной платы (обычно не равен температуре ядер CPU)
    if not sensors and IS_WINDOWS:
        data = _run_powershell(
            "Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
            "| Select-Object InstanceName, CurrentTemperature")
        if data:
            for d in data:
                raw = d.get("CurrentTemperature")
                if raw:
                    sensors.append({"name": "Системная плата (ACPI)",
                                    "celsius": round(raw / 10 - 273.15, 1), "kind": "other"})
            sources.append("ACPI WMI")

    # 4. Видеокарты NVIDIA, если GPU ещё не найден другими источниками
    if not any(s["kind"] == "gpu" for s in sensors):
        gpu = _gpu_temps_nvidia()
        if gpu:
            sensors += gpu
            sources.append("nvidia-smi")

    def mx(kind):
        vals = [s["celsius"] for s in sensors if s["kind"] == kind]
        return max(vals) if vals else None

    if not sensors:
        return {"available": False, "sensors": [], "max": None, "cpu": None, "gpu": None,
                "note": "Датчики недоступны: запустите от имени администратора или LibreHardwareMonitor"}
    return {"available": True, "source": ", ".join(sources), "sensors": sensors,
            "max": max(s["celsius"] for s in sensors), "cpu": mx("cpu"), "gpu": mx("gpu"),
            "note": None if mx("cpu") is not None else
            "Температура CPU недоступна: запустите LibreHardwareMonitor (и программу от имени администратора)"}


# --------------------------------------------------------------------------- #
# SMART
# --------------------------------------------------------------------------- #
_SMART_KEYS = {  # id атрибута -> ключ
    5: "reallocated_sectors", 9: "power_on_hours", 194: "temperature",
    197: "pending_sectors", 198: "uncorrectable_sectors",
}


def _smart_via_smartctl() -> list[dict] | None:
    exe = shutil.which("smartctl")
    if not exe:
        return None
    try:
        scan = subprocess.run([exe, "--scan", "-j"], capture_output=True, text=True, timeout=15)
        devices = json.loads(scan.stdout).get("devices", [])
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        log.debug("smartctl --scan: %s", exc)
        return None
    result = []
    for dev in devices:
        try:
            out = subprocess.run([exe, "-a", "-j", dev["name"]], capture_output=True, text=True, timeout=20)
            j = json.loads(out.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            result.append({"device": dev.get("name"), "error": str(exc)})
            continue
        item = {
            "device": dev.get("name"),
            "model": j.get("model_name"),
            "health": "OK" if j.get("smart_status", {}).get("passed") else "FAIL",
            "temperature": j.get("temperature", {}).get("current"),
            "power_on_hours": j.get("power_on_time", {}).get("hours"),
        }
        for attr in j.get("ata_smart_attributes", {}).get("table", []):
            key = _SMART_KEYS.get(attr.get("id"))
            if key and key not in ("temperature", "power_on_hours"):
                item[key] = attr.get("raw", {}).get("value")
        nvme = j.get("nvme_smart_health_information_log")
        if nvme:
            item["percentage_used"] = nvme.get("percentage_used")
            item["media_errors"] = nvme.get("media_errors")
        result.append(item)
    return result


def _smart_via_windows() -> list[dict] | None:
    disks = _run_powershell(
        "Get-PhysicalDisk | Select-Object DeviceId, FriendlyName, MediaType, BusType, HealthStatus, "
        "OperationalStatus, @{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}}")
    if not disks:
        return None
    # Счётчики надёжности (температура, наработка, ошибки) — часто требуют прав администратора
    counters = _run_powershell(
        "Get-PhysicalDisk | ForEach-Object { $c = $_ | Get-StorageReliabilityCounter; "
        "[pscustomobject]@{DeviceId=$_.DeviceId; Temperature=$c.Temperature; PowerOnHours=$c.PowerOnHours; "
        "ReadErrorsUncorrected=$c.ReadErrorsUncorrected; Wear=$c.Wear} }") or []
    by_id = {str(c.get("DeviceId")): c for c in counters}
    result = []
    for d in disks:
        c = by_id.get(str(d.get("DeviceId")), {})
        op = d.get("OperationalStatus")
        result.append({
            "device": f"PhysicalDrive{d.get('DeviceId')}",
            "model": (d.get("FriendlyName") or "").strip(),
            "media": d.get("MediaType"),
            "bus": d.get("BusType"),
            "size_gb": d.get("SizeGB"),
            "health": {"Healthy": "OK", "Warning": "WARNING", "Unhealthy": "FAIL"}.get(
                d.get("HealthStatus"), str(d.get("HealthStatus"))),
            "operational": ", ".join(op) if isinstance(op, list) else op,
            "temperature": c.get("Temperature") or None,
            "power_on_hours": c.get("PowerOnHours"),
            "uncorrectable_sectors": c.get("ReadErrorsUncorrected"),
            "percentage_used": c.get("Wear"),
        })
    return result


def collect_smart() -> dict:
    data = _smart_via_smartctl()
    source = "smartctl"
    if data is None and IS_WINDOWS:
        data = _smart_via_windows()
        source = "Windows Storage (Get-PhysicalDisk)"
    if data is None:
        return {"available": False, "drives": [],
                "note": "SMART недоступен: установите smartmontools или запустите от имени администратора"}
    return {"available": True, "source": source, "drives": data}


# --------------------------------------------------------------------------- #
# Полный снимок
# --------------------------------------------------------------------------- #
def collect_all(cpu_interval: float = 1.0, smart: dict | None = None) -> dict:
    """Собрать все показатели. ``smart`` можно передать из кэша (SMART опрашивается медленно)."""
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "host": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu": collect_cpu(cpu_interval),
        "memory": collect_memory(),
        "temperature": collect_temperatures(),
        "disks": collect_disks(),
        "smart": smart if smart is not None else collect_smart(),
        "uptime": collect_uptime(),
    }
