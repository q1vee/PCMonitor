"""Интерактивное меню программы."""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

from . import display
from .collectors import collect_all, collect_smart
from .history import PERIODS, HistoryStore, format_period_stats, period_range, period_stats, save_period_report
from .report import build_report
from .storage import MetricsLogger, read_log
from .thresholds import Thresholds, check

from .paths import APP_DIR as ROOT
from .paths import LOGS_DIR, MONITORING_REPORTS_DIR, REPORTS_DIR

LINE = "=" * 40

if os.name == "nt":
    import msvcrt

    def _key_pressed() -> str | None:
        """Вернуть нажатую клавишу без ожидания (Windows)."""
        if not msvcrt.kbhit():
            return None
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):  # служебные клавиши (стрелки, F1…) — двухсимвольные
            msvcrt.getwch()
            return None
        return ch
else:
    import select

    def _key_pressed() -> str | None:
        """Вернуть введённую строку без ожидания (Linux/macOS: ввод по Enter)."""
        r, _, _ = select.select([sys.stdin], [], [], 0)
        if not r:
            return None
        return sys.stdin.readline()[:1] or "\n"


def _clear() -> None:
    display.clear_screen()


def _pause() -> None:
    try:
        input("\nНажмите Enter, чтобы вернуться в меню...")
    except EOFError:
        pass


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return "0"


def _ask_number(prompt: str, default: float | None = None) -> float | None:
    raw = _ask(prompt)
    if not raw and default is not None:
        return default
    try:
        value = float(raw.replace(",", "."))
    except ValueError:
        print("Ошибка: нужно ввести число.")
        return None
    if value <= 0:
        print("Ошибка: число должно быть больше 0.")
        return None
    return value


def daily_log_path(now: datetime | None = None) -> Path:
    """Лог измерений: отдельный CSV-файл на каждые сутки."""
    return LOGS_DIR / f"monitoring_{(now or datetime.now()):%Y-%m-%d}.csv"


# --------------------------------------------------------------------------- #
# 1. Текущий мониторинг
# --------------------------------------------------------------------------- #
def live_monitoring(th: Thresholds, store: HistoryStore) -> None:
    interval = _ask_number("Интервал обновления, с (2–5, Enter — 3): ", 3.0) or 3.0
    interval = min(5.0, max(2.0, interval))

    session = datetime.now().isoformat(timespec="seconds")   # метка сеанса для отчётов
    smart, smart_ts = None, -1e9
    samples = 0
    _clear()
    print("Сбор данных...")
    try:
        while True:
            t0 = time.monotonic()
            if t0 - smart_ts >= 60:            # SMART опрашивается медленно — раз в минуту
                smart, smart_ts = collect_smart(), t0
            snap = collect_all(cpu_interval=1.0, smart=smart)
            alerts = check(snap, th)
            samples += 1

            # после каждого измерения — запись в лог и в историю
            log_path = daily_log_path()
            try:
                MetricsLogger(log_path).write(snap, alerts)
                store.append(snap, alerts, session)
                log_note = f"замер №{samples} записан в лог {log_path.relative_to(ROOT)}"
            except OSError as exc:
                log_note = f"ОШИБКА записи лога: {exc}"

            screen = display.render(snap, alerts)
            footer = (f"\nОбновление каждые {interval:g} с · {log_note}\n"
                      f"Q, Esc или Enter — остановить и вернуться в меню")
            display.redraw(screen + footer)

            # ожидание до следующего обновления с проверкой клавиш
            while time.monotonic() - t0 < interval:
                key = _key_pressed()
                if key is not None and key.lower() in ("q", "й", "\x1b", "\r", "\n"):
                    raise KeyboardInterrupt
                time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    print(f"\n\nМониторинг остановлен. Записано замеров: {samples} (лог: {daily_log_path().relative_to(ROOT)}).")
    if samples:
        try:
            path, stats = save_session_report(store, session)
        except OSError as exc:
            print(f"Не удалось сохранить отчёт мониторинга: {exc}")
        else:
            print()
            print(format_period_stats(stats))
            print(f"\nОтчёт мониторинга сохранён: {path.relative_to(ROOT)}")
            print("Все отчёты мониторинга — в пункте меню «4. Отчёт мониторинга».")
    _pause()


def save_session_report(store: HistoryStore, session: str) -> tuple[Path, dict]:
    """Построить и сохранить отчёт по завершённому сеансу текущего мониторинга."""
    recs = store.load_session(session)
    start, end = (recs[0]["_dt"], recs[-1]["_dt"]) if recs else (datetime.now(),) * 2
    stats = period_stats(recs, start, end, f"Сеанс мониторинга от {start:%d.%m.%Y %H:%M:%S}")
    path = save_period_report(stats, MONITORING_REPORTS_DIR, "txt", name=f"monitoring_{start:%Y%m%d_%H%M%S}")
    return path, stats


# --------------------------------------------------------------------------- #
# Выбор периода для статистики и отчёта
# --------------------------------------------------------------------------- #
PERIOD_KEYS = ("hour", "day", "week")


def choose_data(store: HistoryStore) -> tuple[list[dict], datetime, datetime, str] | None:
    """Спросить период: последний час, 24 часа или 7 дней."""
    bounds = store.bounds()
    print(f"\n{LINE}\n  Выберите период\n{LINE}")
    if bounds:
        print(f"  История: {bounds[0]:%d.%m.%Y %H:%M} — {bounds[1]:%d.%m.%Y %H:%M}")
    else:
        print("  История пока пуста — сначала запустите «Текущий мониторинг».")
    for i, key in enumerate(PERIOD_KEYS, 1):
        print(f"  {i}. {PERIODS[key][0]}")
    print(f"\n  0. Назад\n{LINE}")
    choice = _ask("Выберите период: ")
    if choice == "0":
        return None
    if choice not in ("1", "2", "3"):
        print("Неверный выбор.")
        return None
    start, end, title = period_range(PERIOD_KEYS[int(choice) - 1])
    return store.load(start, end), start, end, title


# --------------------------------------------------------------------------- #
# 2. Мониторинг за период
# --------------------------------------------------------------------------- #
def period_monitoring(store: HistoryStore) -> None:
    _clear()
    data = choose_data(store)
    if data:
        recs, start, end, title = data
        print()
        print(format_period_stats(period_stats(recs, start, end, title)))
    _pause()


# --------------------------------------------------------------------------- #
# 3. Создать отчёт
# --------------------------------------------------------------------------- #
def create_report(store: HistoryStore) -> None:
    _clear()
    data = choose_data(store)
    if not data:
        _pause()
        return
    recs, start, end, title = data
    stats = period_stats(recs, start, end, title)
    print("\n  Формат отчёта:\n  1. TXT (текст)\n  2. JSON\n  3. CSV (для Excel)")
    fmt = {"1": "txt", "2": "json", "3": "csv"}.get(_ask("Выберите формат (Enter — TXT): ") or "1", "txt")
    try:
        path = save_period_report(stats, REPORTS_DIR, fmt)
    except OSError as exc:
        print(f"Ошибка сохранения отчёта: {exc}")
        _pause()
        return
    print()
    print(format_period_stats(stats))
    print(f"\nОтчёт сохранён: {path}")
    if os.name == "nt" and _ask("Открыть отчёт? (д/н): ").lower() in ("д", "y", "да", "yes"):
        try:
            os.startfile(path)  # noqa: S606 — открыть файл программой по умолчанию
        except OSError as exc:
            print(f"Не удалось открыть: {exc}")
    _pause()


# --------------------------------------------------------------------------- #
# 4. Отчёт мониторинга
# --------------------------------------------------------------------------- #
def list_monitoring_reports(folder: Path = MONITORING_REPORTS_DIR) -> list[Path]:
    """Сохранённые отчёты сеансов мониторинга, новые первыми."""
    return sorted(folder.glob("monitoring_*.txt"), reverse=True) if folder.exists() else []


def _report_summary(path: Path) -> str:
    """Дата сеанса и число измерений для строки списка."""
    try:
        when = datetime.strptime(path.stem, "monitoring_%Y%m%d_%H%M%S").strftime("%d.%m.%Y %H:%M:%S")
    except ValueError:
        when = path.stem
    count = "?"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "Количество измерений" in line:
                count = line.split(":")[-1].strip()
                break
    except OSError:
        pass
    return f"{when}   замеров: {count}"


def monitoring_reports() -> None:
    _clear()
    reports = list_monitoring_reports()
    print(f"{LINE}\n  Отчёты мониторинга\n{LINE}")
    if not reports:
        print("  Отчётов пока нет. Отчёт сохраняется автоматически\n"
              "  после каждого сеанса «1. Текущий мониторинг».")
        _pause()
        return
    shown = reports[:20]
    for i, path in enumerate(shown, 1):
        print(f"  {i:>2}. {_report_summary(path)}")
    if len(reports) > len(shown):
        print(f"  … и ещё {len(reports) - len(shown)} (папка {MONITORING_REPORTS_DIR.relative_to(ROOT)})")
    print(f"\n   0. Назад\n{LINE}")
    raw = _ask("Номер отчёта (Enter — последний): ") or "1"
    if raw == "0":
        return
    if not raw.isdigit() or not 1 <= int(raw) <= len(shown):
        print("Неверный выбор.")
        _pause()
        return
    path = shown[int(raw) - 1]
    _clear()
    try:
        print(path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"Не удалось прочитать отчёт: {exc}")
        _pause()
        return
    print(f"Файл: {path}")
    if os.name == "nt" and _ask("Открыть файл отчёта? (д/н): ").lower() in ("д", "y", "да", "yes"):
        try:
            os.startfile(path)  # noqa: S606
        except OSError as exc:
            print(f"Не удалось открыть: {exc}")
    _pause()


# --------------------------------------------------------------------------- #
# 5. Логи мониторинга
# --------------------------------------------------------------------------- #
LOG_ROWS_ON_SCREEN = 30


def _log_last_time(path: Path) -> datetime:
    """Время последнего замера в логе (если не прочитать — время изменения файла)."""
    try:
        with path.open(encoding="utf-8-sig", errors="replace") as f:
            last = ""
            for line in f:
                if line.strip():
                    last = line
        if path.suffix.lower() == ".csv":
            stamp = last.split(";", 1)[0]
        else:
            import json
            stamp = json.loads(last).get("timestamp", "")
        return datetime.fromisoformat(stamp)
    except (OSError, ValueError, AttributeError):
        return datetime.fromtimestamp(path.stat().st_mtime)


def list_logs(folder: Path = LOGS_DIR) -> list[Path]:
    """Файлы логов (CSV и JSONL), новые первыми — по времени последнего замера."""
    if not folder.exists():
        return []
    files = [p for p in folder.iterdir() if p.suffix.lower() in (".csv", ".jsonl") and p.is_file()]
    return sorted(files, key=_log_last_time, reverse=True)


def _cell(v, width: int, digits: int = 1) -> str:
    if v is None or v == "":
        text = "—"
    elif isinstance(v, float):
        text = f"{v:.{digits}f}"
    else:
        text = str(v)
    return text[:width].rjust(width)


def format_log_table(rows: list[dict]) -> str:
    """Таблица замеров лога для вывода в консоль."""
    head = (f"{'Время':<19} {'CPU %':>6} {'RAM %':>6} {'ОЗУ ГБ':>7} {'Темп °C':>7} "
            f"{'Мин. своб. %':>12} {'Диск':>5} {'SMART':>6} {'Предупр.':>8}")
    lines = [head, "-" * len(head)]
    for r in rows:
        alerts = [a for a in (r.get("alerts") or "").split("|") if a.strip()]
        lines.append(
            f"{str(r.get('timestamp', '')).replace('T', ' ')[:19]:<19} {_cell(r['cpu_percent'], 6)} "
            f"{_cell(r['ram_percent'], 6)} {_cell(r['ram_used_gb'], 7, 2)} {_cell(r['temp_max'], 7)} "
            f"{_cell(r['disk_min_free_percent'], 12)} {_cell(r.get('disk_min_free_mount'), 5)} "
            f"{_cell(r.get('smart_status'), 6)} {_cell(len(alerts), 8)}")
    return "\n".join(lines)


def _log_rows_count(path: Path) -> int:
    try:
        with path.open(encoding="utf-8-sig", errors="replace") as f:
            n = sum(1 for line in f if line.strip())
    except OSError:
        return 0
    return n - 1 if path.suffix.lower() == ".csv" else n


def view_logs() -> None:
    _clear()
    logs = list_logs()
    print(f"{LINE}\n  Логи мониторинга\n{LINE}")
    print(f"  Папка: {LOGS_DIR}")
    if not logs:
        print("\n  Логов пока нет. Они появляются после «1. Текущий мониторинг».")
        _pause()
        return
    shown = logs[:20]
    for i, p in enumerate(shown, 1):
        print(f"  {i:>2}. {p.name:<34} последний замер {_log_last_time(p):%d.%m.%Y %H:%M}   "
              f"замеров: {_log_rows_count(p)}")
    print(f"\n   0. Назад\n{LINE}")
    raw = _ask("Номер лога (Enter — последний): ") or "1"
    if raw == "0":
        return
    if not raw.isdigit() or not 1 <= int(raw) <= len(shown):
        print("Неверный выбор.")
        _pause()
        return
    path = shown[int(raw) - 1]
    try:
        rows = read_log(path)
    except (OSError, ValueError) as exc:
        print(f"Не удалось прочитать лог: {exc}")
        _pause()
        return
    _clear()
    print(f"Лог: {path.name}   всего замеров: {len(rows)}\n")
    if not rows:
        print("Лог пуст.")
        _pause()
        return
    tail = rows[-LOG_ROWS_ON_SCREEN:]
    if len(rows) > len(tail):
        print(f"(показаны последние {len(tail)} замеров)")
    print(format_log_table(tail))

    rep = build_report(rows)
    def s(key):
        st = rep[key]
        return f"ср {st['avg']} / макс {st['max']}" if st else "нет данных"
    print(f"\nИтог: период {rep['period_start'].replace('T', ' ')} — {rep['period_end'].replace('T', ' ')}, "
          f"замеров {rep['samples']}")
    print(f"      CPU % {s('cpu_percent')} · RAM % {s('ram_percent')} · темп. °C {s('temp_max')} · "
          f"замеров с предупреждениями: {rep['samples_with_alerts']}")
    if os.name == "nt" and _ask("\nОткрыть лог в Excel? (д/н): ").lower() in ("д", "y", "да", "yes"):
        try:
            os.startfile(path)  # noqa: S606
        except OSError as exc:
            print(f"Не удалось открыть: {exc}")
    _pause()


# --------------------------------------------------------------------------- #
# Главное меню
# --------------------------------------------------------------------------- #
def run_menu(th: Thresholds, store: HistoryStore | None = None) -> int:
    store = store or HistoryStore()
    actions = {"1": lambda: live_monitoring(th, store),
               "2": lambda: period_monitoring(store),
               "3": lambda: create_report(store),
               "4": monitoring_reports,
               "5": view_logs}
    while True:
        _clear()
        print(f"{LINE}\n   МОНИТОРИНГ СОСТОЯНИЯ ПК\n{LINE}")
        print("1. Текущий мониторинг\n2. Мониторинг за период\n3. Создать отчёт\n4. Отчёт мониторинга\n5. Логи мониторинга\n\n0. Выход\n")
        print(LINE)
        try:
            choice = input("Выберите действие: ").strip()
        except (EOFError, KeyboardInterrupt):
            choice = "0"
        if choice == "0":
            print("Выход.")
            return 0
        action = actions.get(choice)
        if action is None:
            print("Неверный выбор. Введите число от 0 до 5.")
            time.sleep(1.2)
            continue
        try:
            action()
        except KeyboardInterrupt:
            continue
