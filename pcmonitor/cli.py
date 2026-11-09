"""Интерфейс командной строки."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from . import __version__, display
from .collectors import collect_all, collect_smart
from .history import HistoryStore
from .menu import run_menu
from .report import build_report, format_report, save_report
from .storage import MetricsLogger, read_log
from .thresholds import Thresholds, check


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pcmonitor", description="Мониторинг состояния ПК: CPU, ОЗУ, температура, "
                                "диски, SMART, uptime; логирование и отчёт за период.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="отладочные сообщения")
    p.add_argument("--no-color", action="store_true", help="без цветного вывода")
    p.add_argument("--thresholds", metavar="FILE", help="JSON-файл с пороговыми значениями")
    sub = p.add_subparsers(dest="command")  # без команды запускается интерактивное меню
    sub.add_parser("menu", help="интерактивное меню (по умолчанию)")

    s = sub.add_parser("snapshot", help="однократный снимок состояния")
    s.add_argument("--json", action="store_true", help="вывести снимок в формате JSON")

    m = sub.add_parser("monitor", help="периодический мониторинг с записью в лог")
    m.add_argument("-i", "--interval", type=float, default=10, help="период замеров, с (по умолчанию 10)")
    m.add_argument("-d", "--duration", type=float, default=0,
                   help="длительность наблюдения, с (0 — до Ctrl+C)")
    m.add_argument("-o", "--output", default=None, help="файл лога .csv или .jsonl (по умолчанию logs/metrics_<дата>.csv)")
    m.add_argument("--smart-every", type=float, default=300, help="как часто обновлять SMART, с (по умолчанию 300)")
    m.add_argument("--quiet", action="store_true", help="не перерисовывать экран, печатать только предупреждения")
    m.add_argument("--report", metavar="FILE", help="по завершении сохранить отчёт (.txt или .json)")

    r = sub.add_parser("report", help="итоговый отчёт по файлу лога")
    r.add_argument("log", help="файл лога .csv или .jsonl")
    r.add_argument("-o", "--output", help="сохранить отчёт в файл (.txt или .json)")
    return p


def cmd_snapshot(args, th: Thresholds) -> int:
    snap = collect_all()
    alerts = check(snap, th)
    if args.json:
        print(json.dumps(dict(snap, alerts=[a.__dict__ for a in alerts]), ensure_ascii=False, indent=2))
    else:
        print(display.render(snap, alerts))
    return 2 if any(a.level == "CRITICAL" for a in alerts) else 0


def cmd_monitor(args, th: Thresholds) -> int:
    if args.interval <= 0:
        print("Ошибка: интервал должен быть больше 0", file=sys.stderr)
        return 1
    out = Path(args.output or f"logs/metrics_{datetime.now():%Y%m%d_%H%M%S}.csv")
    logger = MetricsLogger(out)
    history = HistoryStore()
    print(f"Мониторинг запущен: интервал {args.interval} с, "
          f"{'до Ctrl+C' if not args.duration else f'длительность {args.duration} с'}. Лог: {out.resolve()}")
    started = time.monotonic()
    smart, smart_ts = None, -1e9
    samples = 0
    try:
        while True:
            t0 = time.monotonic()
            if t0 - smart_ts >= args.smart_every:
                smart, smart_ts = collect_smart(), t0
            snap = collect_all(cpu_interval=min(1.0, args.interval), smart=smart)
            alerts = check(snap, th)
            try:
                logger.write(snap, alerts)
                history.append(snap, alerts)
            except OSError as exc:
                print(f"Ошибка записи лога: {exc}", file=sys.stderr)
            samples += 1
            if args.quiet:
                for a in alerts:
                    print(f"{snap['timestamp']} {a}")
            else:
                print("\033[2J\033[H" + display.render(snap, alerts))
                print(f"\nЗамеров: {samples} · лог: {out} · Ctrl+C — остановить")
            if args.duration and time.monotonic() - started >= args.duration:
                break
            time.sleep(max(0.0, args.interval - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")

    print(f"Записано замеров: {samples}. Файл: {out.resolve()}")
    if samples:
        rep = build_report(read_log(out))
        print(format_report(rep))
        if args.report:
            print(f"Отчёт сохранён: {save_report(rep, args.report).resolve()}")
    return 0


def cmd_report(args, th: Thresholds) -> int:
    rep = build_report(read_log(args.log))
    print(format_report(rep))
    if args.output:
        print(f"\nОтчёт сохранён: {save_report(rep, args.output).resolve()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Корректный вывод кириллицы и символов псевдографики в любой консоли
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if args.no_color or not sys.stdout.isatty():
        display.USE_COLOR = False
    try:
        th = Thresholds.load(args.thresholds)
        if args.command in (None, "menu"):
            return run_menu(th)
        handler = {"snapshot": cmd_snapshot, "monitor": cmd_monitor, "report": cmd_report}[args.command]
        return handler(args, th)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, TypeError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
