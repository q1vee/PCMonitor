"""Итоговый отчёт за период наблюдения."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean


def _stats(values: list[float]) -> dict | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return {"min": round(min(vals), 1), "avg": round(mean(vals), 1), "max": round(max(vals), 1)}


def build_report(rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("Лог пуст — нет данных для отчёта")
    start, end = rows[0]["timestamp"], rows[-1]["timestamp"]
    try:
        duration = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    except (TypeError, ValueError):
        duration = None

    alert_counter: Counter = Counter()
    alert_samples = 0
    for r in rows:
        alerts = [a.strip() for a in (r.get("alerts") or "").split("|") if a.strip()]
        if alerts:
            alert_samples += 1
        alert_counter.update(alerts)

    return {
        "period_start": start,
        "period_end": end,
        "duration": str(duration) if duration is not None else "н/д",
        "samples": len(rows),
        "cpu_percent": _stats([r["cpu_percent"] for r in rows]),
        "ram_percent": _stats([r["ram_percent"] for r in rows]),
        "temp_max": _stats([r["temp_max"] for r in rows]),
        "disk_min_free_percent": _stats([r["disk_min_free_percent"] for r in rows]),
        "smart_status": Counter(r.get("smart_status") or "N/A" for r in rows).most_common(),
        "samples_with_alerts": alert_samples,
        "top_alerts": alert_counter.most_common(10),
    }


def format_report(rep: dict) -> str:
    def line(title: str, st: dict | None, unit: str) -> str:
        if not st:
            return f"  {title:<28} нет данных"
        return f"  {title:<28} мин {st['min']:>6}{unit}   ср {st['avg']:>6}{unit}   макс {st['max']:>6}{unit}"

    out = [
        "=" * 72,
        "  ИТОГОВЫЙ ОТЧЁТ ЗА ПЕРИОД НАБЛЮДЕНИЯ",
        "=" * 72,
        f"  Период:      {rep['period_start']}  —  {rep['period_end']}",
        f"  Длительность: {rep['duration']}    Замеров: {rep['samples']}",
        "-" * 72,
        line("Загрузка CPU", rep["cpu_percent"], " %"),
        line("Занятость ОЗУ", rep["ram_percent"], " %"),
        line("Макс. температура", rep["temp_max"], " °C"),
        line("Мин. свободно на диске", rep["disk_min_free_percent"], " %"),
        f"  {'Статус SMART':<28} " + ", ".join(f"{s}: {n}" for s, n in rep["smart_status"]),
        "-" * 72,
        f"  Замеров с предупреждениями: {rep['samples_with_alerts']} из {rep['samples']}",
    ]
    if rep["top_alerts"]:
        out.append("  Частые предупреждения:")
        out += [f"    {n:>4} × {msg}" for msg, n in rep["top_alerts"]]
    else:
        out.append("  Превышений пороговых значений не зафиксировано.")
    verdict = "ТРЕБУЕТСЯ ВНИМАНИЕ" if rep["samples_with_alerts"] else "СИСТЕМА В НОРМЕ"
    out += ["-" * 72, f"  Заключение: {verdict}", "=" * 72]
    return "\n".join(out)


def save_report(rep: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        import json
        path.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        path.write_text(format_report(rep), encoding="utf-8")
    return path
