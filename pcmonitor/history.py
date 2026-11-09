"""История мониторинга и статистика за период.

Каждый замер дописывается одной компактной строкой JSON в ``data/history.jsonl``.
Из этой истории строится статистика за выбранный период (час, сутки, неделя,
произвольный интервал) и итоговые отчёты.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean

DEFAULT_HISTORY = Path(__file__).resolve().parents[1] / "data" / "history.jsonl"


def compact(snapshot: dict, alerts: list, session: str | None = None) -> dict:
    """Сжать снимок до записи истории (~300 байт). ``session`` — метка сеанса мониторинга."""
    temp = snapshot.get("temperature", {})
    drives = snapshot.get("smart", {}).get("drives", [])
    statuses = {d.get("health") for d in drives if d.get("health")}
    return {
        "ts": snapshot.get("timestamp"),
        "session": session,
        "cpu": snapshot.get("cpu", {}).get("percent"),
        "ram": snapshot.get("memory", {}).get("percent"),
        "ram_used_gb": snapshot.get("memory", {}).get("used_gb"),
        "t_cpu": temp.get("cpu"),
        "t_gpu": temp.get("gpu"),
        "t_max": temp.get("max"),
        "t_other": max((s["celsius"] for s in temp.get("sensors", []) if s.get("kind") == "other"), default=None),
        "disks": {d["mount"]: {"free": d["free_gb"], "total": d["total_gb"]}
                  for d in snapshot.get("disks", []) if "free_gb" in d},
        "smart": "FAIL" if "FAIL" in statuses else "WARNING" if "WARNING" in statuses else (
            "OK" if statuses else None),
        "alerts": [str(a) for a in alerts],
    }


class HistoryStore:
    def __init__(self, path: str | Path = DEFAULT_HISTORY):
        self.path = Path(path)

    def append(self, snapshot: dict, alerts: list, session: str | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(compact(snapshot, alerts, session), ensure_ascii=False) + "\n")

    def load_last(self, n: int) -> list[dict]:
        """Последние n измерений."""
        return self.load()[-n:] if n > 0 else []

    def load_session(self, session: str) -> list[dict]:
        return [r for r in self.load() if r.get("session") == session]

    def sessions(self, limit: int = 10) -> list[dict]:
        """Последние сеансы мониторинга: метка, начало, конец, число замеров (новые первыми)."""
        info: dict[str, dict] = {}
        for r in self.load():
            sid = r.get("session")
            if not sid:
                continue
            s = info.setdefault(sid, {"session": sid, "start": r["_dt"], "end": r["_dt"], "count": 0})
            s["end"] = r["_dt"]
            s["count"] += 1
        return sorted(info.values(), key=lambda s: s["start"], reverse=True)[:limit]

    def load(self, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
        """Записи в интервале [start, end]. Повреждённые строки пропускаются."""
        if not self.path.exists():
            return []
        out = []
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    ts = datetime.fromisoformat(rec["ts"])
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
                if (start and ts < start) or (end and ts > end):
                    continue
                rec["_dt"] = ts
                out.append(rec)
        out.sort(key=lambda r: r["_dt"])
        return out

    def bounds(self) -> tuple[datetime, datetime] | None:
        recs = self.load()
        return (recs[0]["_dt"], recs[-1]["_dt"]) if recs else None


# --------------------------------------------------------------------------- #
# Периоды
# --------------------------------------------------------------------------- #
PERIODS = {
    "hour": ("Последний час", timedelta(hours=1)),
    "day": ("Последние 24 часа", timedelta(hours=24)),
    "week": ("Последние 7 дней", timedelta(days=7)),
}


def period_range(key: str, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    title, delta = PERIODS[key]
    now = now or datetime.now()
    return now - delta, now, title


def last_range(hours: float = 0, days: float = 0, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    """Интервал «последние N часов / N дней»."""
    now = now or datetime.now()
    if hours:
        return now - timedelta(hours=hours), now, f"Последние {hours:g} ч"
    return now - timedelta(days=days), now, f"Последние {days:g} дн."


def parse_datetime(text: str) -> datetime:
    """Разобрать дату в форматах ДД.ММ.ГГГГ [ЧЧ:ММ] или ГГГГ-ММ-ДД [ЧЧ:ММ]."""
    text = text.strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise ValueError(f"Неверный формат даты: «{text}». Пример: 09.11.2026 14:30")


# --------------------------------------------------------------------------- #
# Статистика
# --------------------------------------------------------------------------- #
def _vals(recs, key):
    return [r[key] for r in recs if isinstance(r.get(key), (int, float))]


def _agg(values, *funcs):
    return {f.__name__: (round(f(values), 1) if values else None) for f in funcs}


def period_stats(recs: list[dict], start: datetime, end: datetime, title: str = "") -> dict:
    cpu, ram = _vals(recs, "cpu"), _vals(recs, "ram")
    t_cpu, t_gpu, t_other = _vals(recs, "t_cpu"), _vals(recs, "t_gpu"), _vals(recs, "t_other")

    disks = {}
    for mount in sorted({m for r in recs for m in r.get("disks", {})}):
        series = [(r["_dt"], r["disks"][mount]["free"]) for r in recs if mount in r.get("disks", {})]
        first, last = series[0][1], series[-1][1]
        disks[mount] = {"free_start_gb": first, "free_end_gb": last,
                        "change_gb": round(last - first, 2),
                        "free_min_gb": min(v for _, v in series),
                        "total_gb": recs[-1]["disks"].get(mount, {}).get("total")}

    alert_counter = Counter(a for r in recs for a in r.get("alerts", []))
    max_cpu_rec = max(recs, key=lambda r: r.get("cpu") or 0) if cpu else None

    return {
        "title": title,
        "period_start": start.isoformat(timespec="seconds"),
        "period_end": end.isoformat(timespec="seconds"),
        "first_measurement": recs[0]["ts"] if recs else None,
        "last_measurement": recs[-1]["ts"] if recs else None,
        "measurements": len(recs),
        "cpu": {"avg": _agg(cpu, mean)["mean"], "max": _agg(cpu, max)["max"],
                "max_at": max_cpu_rec["ts"] if max_cpu_rec else None},
        "ram": {"avg": _agg(ram, mean)["mean"], "max": _agg(ram, max)["max"]},
        "temp_cpu": _agg(t_cpu, min, max),
        "temp_gpu": _agg(t_gpu, min, max),
        "temp_other": _agg(t_other, min, max),
        "disks": disks,
        "warnings_total": sum(alert_counter.values()),
        "measurements_with_warnings": sum(1 for r in recs if r.get("alerts")),
        "top_warnings": alert_counter.most_common(5),
        "smart_statuses": dict(Counter(r.get("smart") or "н/д" for r in recs)),
    }


def _fmt(v, unit=""):
    return "нет данных" if v is None else f"{v}{unit}"


def format_period_stats(s: dict) -> str:
    def t(span):
        if span["min"] is None:
            return "нет данных"
        return f"мин {span['min']} °C   макс {span['max']} °C"

    head = f"  {s['title']}: " if s["title"] else "  Период: "
    L = [
        "=" * 64,
        "  СТАТИСТИКА ЗА ПЕРИОД",
        "=" * 64,
        head + f"{s['period_start'].replace('T', ' ')} — {s['period_end'].replace('T', ' ')}",
        f"  Количество измерений:        {s['measurements']}",
    ]
    if not s["measurements"]:
        L += ["-" * 64, "  За выбранный период в истории нет данных.",
              "  Запустите «Текущий мониторинг», чтобы начать сбор истории.", "=" * 64]
        return "\n".join(L)
    L += [
        f"  Первое / последнее измерение: {s['first_measurement'].replace('T', ' ')} / "
        f"{s['last_measurement'].replace('T', ' ')}",
        "-" * 64,
        f"  CPU   средняя загрузка:      {_fmt(s['cpu']['avg'], ' %')}",
        f"        максимальная загрузка: {_fmt(s['cpu']['max'], ' %')}"
        + (f"  ({s['cpu']['max_at'].replace('T', ' ')})" if s['cpu']['max_at'] else ""),
        f"  RAM   среднее использование: {_fmt(s['ram']['avg'], ' %')}",
        f"        максимальное:          {_fmt(s['ram']['max'], ' %')}",
        "-" * 64,
        f"  Температура CPU:  {t(s['temp_cpu'])}",
        f"  Температура GPU:  {t(s['temp_gpu'])}",
    ]
    if s["temp_other"]["min"] is not None:
        L.append(f"  Системная плата:  {t(s['temp_other'])}")
    L += ["-" * 64, "  Свободное место на дисках (начало → конец периода):"]
    for mount, d in s["disks"].items():
        sign = "+" if d["change_gb"] > 0 else ""
        L.append(f"    {mount:<5} {d['free_start_gb']:>8} → {d['free_end_gb']:<8} ГБ   "
                 f"изменение {sign}{d['change_gb']} ГБ   минимум {d['free_min_gb']} ГБ")
    L += [
        "-" * 64,
        f"  Предупреждений обнаружено:   {s['warnings_total']} "
        f"(в {s['measurements_with_warnings']} из {s['measurements']} измерений)",
    ]
    for msg, n in s["top_warnings"]:
        L.append(f"    {n:>5} × {msg}")
    L.append("  Состояние SMART: " + ", ".join(f"{k}: {v}" for k, v in s["smart_statuses"].items()))
    L.append("=" * 64)
    return "\n".join(L)


def save_period_report(s: dict, folder: str | Path, fmt: str = "txt", name: str | None = None) -> Path:
    """Сохранить отчёт за период в TXT, JSON или CSV."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name or f'report_{datetime.now():%Y%m%d_%H%M%S}'}.{fmt}"
    if fmt == "json":
        path.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    elif fmt == "csv":
        rows = [
            ("Период", f"{s['period_start']} — {s['period_end']}"),
            ("Количество измерений", s["measurements"]),
            ("CPU средняя, %", s["cpu"]["avg"]), ("CPU максимальная, %", s["cpu"]["max"]),
            ("RAM средняя, %", s["ram"]["avg"]), ("RAM максимальная, %", s["ram"]["max"]),
            ("Температура CPU мин, °C", s["temp_cpu"]["min"]), ("Температура CPU макс, °C", s["temp_cpu"]["max"]),
            ("Температура GPU мин, °C", s["temp_gpu"]["min"]), ("Температура GPU макс, °C", s["temp_gpu"]["max"]),
            *[(f"Диск {m}: изменение свободного места, ГБ", d["change_gb"]) for m, d in s["disks"].items()],
            ("Предупреждений", s["warnings_total"]),
        ]
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Показатель", "Значение"])
            for k, v in rows:
                w.writerow([k, "" if v is None else v])
    else:
        path.write_text(format_period_stats(s) + "\n", encoding="utf-8")
    return path
