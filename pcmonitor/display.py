"""Форматированный вывод снимка в консоль."""

from __future__ import annotations

import os

# Включаем ANSI-цвета в консоли Windows 10+
if os.name == "nt":
    os.system("")

RESET, BOLD, RED, YEL, GRN, DIM = "\033[0m", "\033[1m", "\033[91m", "\033[93m", "\033[92m", "\033[2m"
USE_COLOR = True


def _c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}" if USE_COLOR else text


def bar(percent: float | None, width: int = 24, warn: float = 75, crit: float = 90) -> str:
    if percent is None:
        return "н/д"
    filled = int(round(width * min(percent, 100) / 100))
    color = RED if percent >= crit else YEL if percent >= warn else GRN
    return _c("█" * filled, color) + _c("░" * (width - filled), DIM) + f" {percent:5.1f}%"


def render(snap: dict, alerts: list) -> str:
    L: list[str] = []
    L.append(_c(f"══ Мониторинг ПК · {snap['host']} · {snap['os']} · {snap['timestamp']} ══", BOLD))

    cpu = snap["cpu"]
    if "error" in cpu:
        L.append(f"CPU        ошибка: {cpu['error']}")
    else:
        L.append(f"CPU        {bar(cpu['percent'])}   {cpu['cores_physical']} ядер / {cpu['cores_logical']} потоков, "
                 f"{cpu['freq_mhz'] or '?'} МГц")

    mem = snap["memory"]
    if "error" in mem:
        L.append(f"ОЗУ        ошибка: {mem['error']}")
    else:
        L.append(f"ОЗУ        {bar(mem['percent'])}   {mem['used_gb']} / {mem['total_gb']} ГБ, подкачка {mem['swap_percent']}%")

    t = snap["temperature"]
    if t["available"]:
        L.append(f"Температура (источник: {t['source']})")
        for s in t["sensors"][:12]:
            c = s["celsius"]
            col = RED if c > 85 else YEL if c > 70 else GRN
            L.append(f"  {s['name'][:40]:<40} {_c(f'{c:5.1f} °C', col)}")
    else:
        L.append(f"Температура {_c(t['note'], DIM)}")

    L.append("Диски")
    for d in snap["disks"]:
        if "error" in d:
            L.append(f"  {d.get('mount', '?'):<6} ошибка: {d['error']}")
        else:
            L.append(f"  {d['mount']:<6} {bar(d['percent'], 20, 80, 90)}  свободно {d['free_gb']} из {d['total_gb']} ГБ ({d['fstype']})")

    sm = snap["smart"]
    if sm["available"]:
        L.append(f"SMART (источник: {sm['source']})")
        for drv in sm["drives"]:
            if "error" in drv:
                L.append(f"  {drv.get('device')}: ошибка {drv['error']}")
                continue
            h = drv.get("health")
            col = GRN if h == "OK" else RED if h == "FAIL" else YEL
            extra = []
            for key, label, unit in (("media", "", ""), ("size_gb", "", " ГБ"), ("temperature", "t=", " °C"),
                                     ("power_on_hours", "наработка ", " ч"),
                                     ("reallocated_sectors", "переназн. ", ""),
                                     ("percentage_used", "износ ", "%")):
                v = drv.get(key)
                if v not in (None, "", "Unspecified"):
                    extra.append(f"{label}{v}{unit}")
            L.append(f"  {(drv.get('model') or drv.get('device'))[:34]:<34} {_c(f'{h:<7}', col)} {', '.join(extra)}")
    else:
        L.append(f"SMART      {_c(sm['note'], DIM)}")

    up = snap["uptime"]
    L.append(f"Uptime     {up.get('human', up.get('error'))} (загрузка: {up.get('boot_time', '?')})")

    if alerts:
        L.append(_c("Предупреждения:", BOLD))
        for a in alerts:
            L.append("  " + _c(str(a), RED if a.level == "CRITICAL" else YEL))
    else:
        L.append(_c("Пороговые значения не превышены.", GRN))
    return "\n".join(L)
