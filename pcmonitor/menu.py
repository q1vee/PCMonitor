"""Интерактивное меню программы."""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

from . import display
from .collectors import collect_all, collect_smart
from .history import (HistoryStore, format_period_stats, last_range, parse_datetime, period_range,
                      period_stats, save_period_report)
from .storage import MetricsLogger
from .thresholds import Thresholds, check

ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports"
LOGS_DIR = ROOT / "logs"
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
    print("\033[2J\033[H", end="")


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
            # перерисовка без мерцания: курсор в начало, строки с очисткой хвоста
            out = "\n".join(line + "\033[K" for line in (screen + footer).split("\n"))
            print("\033[H" + out + "\033[J", end="", flush=True)

            # ожидание до следующего обновления с проверкой клавиш
            while time.monotonic() - t0 < interval:
                key = _key_pressed()
                if key is not None and key.lower() in ("q", "й", "\x1b", "\r", "\n"):
                    raise KeyboardInterrupt
                time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    print(f"\n\nМониторинг остановлен. Записано замеров: {samples} (лог: {daily_log_path().relative_to(ROOT)}).")
    print("Отчёт по этому сеансу: «3. Создать отчёт» → «Сеанс текущего мониторинга».")
    time.sleep(2)


# --------------------------------------------------------------------------- #
# Выбор данных для статистики и отчёта
# --------------------------------------------------------------------------- #
DATA_CHOICES = [
    "Последний час",
    "Последние 24 часа",
    "Последние 7 дней",
    "Последние N часов",
    "Последние N дней",
    "Последние N измерений",
    "Сеанс текущего мониторинга",
    "Произвольный период",
]


def _span(recs: list[dict]) -> tuple[datetime, datetime]:
    if recs:
        return recs[0]["_dt"], recs[-1]["_dt"]
    now = datetime.now()
    return now, now


def _choose_session(store: HistoryStore) -> tuple[list[dict], str] | None:
    sessions = store.sessions()
    if not sessions:
        print("Сеансов текущего мониторинга пока нет.")
        return None
    print("\n  Последние сеансы текущего мониторинга:")
    for i, s in enumerate(sessions, 1):
        print(f"  {i}. {s['start']:%d.%m.%Y %H:%M:%S} — {s['end']:%H:%M:%S}   замеров: {s['count']}")
    raw = _ask("Номер сеанса (Enter — последний): ") or "1"
    if not raw.isdigit() or not 1 <= int(raw) <= len(sessions):
        print("Неверный выбор.")
        return None
    s = sessions[int(raw) - 1]
    return store.load_session(s["session"]), f"Сеанс мониторинга от {s['start']:%d.%m.%Y %H:%M:%S}"


def choose_data(store: HistoryStore) -> tuple[list[dict], datetime, datetime, str] | None:
    """Спросить, за какой период или по каким измерениям строить статистику."""
    bounds = store.bounds()
    print(f"\n{LINE}\n  Данные для статистики\n{LINE}")
    if bounds:
        print(f"  История: {bounds[0]:%d.%m.%Y %H:%M} — {bounds[1]:%d.%m.%Y %H:%M}")
    else:
        print("  История пока пуста — сначала запустите «Текущий мониторинг».")
    for i, title in enumerate(DATA_CHOICES, 1):
        print(f"  {i}. {title}")
    print(f"\n  0. Назад\n{LINE}")
    choice = _ask("Выберите вариант: ")

    if choice == "0":
        return None
    if choice in ("1", "2", "3"):
        start, end, title = period_range(("hour", "day", "week")[int(choice) - 1])
        return store.load(start, end), start, end, title
    if choice == "4":
        n = _ask_number("Сколько последних часов? ")
        if n is None:
            return None
        start, end, title = last_range(hours=n)
        return store.load(start, end), start, end, title
    if choice == "5":
        n = _ask_number("Сколько последних дней? ")
        if n is None:
            return None
        start, end, title = last_range(days=n)
        return store.load(start, end), start, end, title
    if choice == "6":
        n = _ask_number("Сколько последних измерений? ")
        if n is None:
            return None
        recs = store.load_last(int(n))
        return (recs, *_span(recs), f"Последние измерения ({int(n)} шт.)")
    if choice == "7":
        res = _choose_session(store)
        if not res:
            return None
        recs, title = res
        return (recs, *_span(recs), title)
    if choice == "8":
        print("Формат даты: ДД.ММ.ГГГГ ЧЧ:ММ (время можно не указывать)")
        try:
            start = parse_datetime(_ask("  Начало периода: "))
            end_raw = _ask("  Конец периода (Enter — сейчас): ")
            end = parse_datetime(end_raw) if end_raw else datetime.now()
            if len(end_raw) == 10:     # указана только дата — до конца дня
                end = end.replace(hour=23, minute=59, second=59)
        except ValueError as exc:
            print(f"Ошибка: {exc}")
            return None
        if start > end:
            print("Ошибка: начало периода позже конца.")
            return None
        return store.load(start, end), start, end, "Произвольный период"
    print("Неверный выбор.")
    return None


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
# Главное меню
# --------------------------------------------------------------------------- #
def run_menu(th: Thresholds, store: HistoryStore | None = None) -> int:
    store = store or HistoryStore()
    actions = {"1": lambda: live_monitoring(th, store),
               "2": lambda: period_monitoring(store),
               "3": lambda: create_report(store)}
    while True:
        _clear()
        print(f"{LINE}\n   МОНИТОРИНГ СОСТОЯНИЯ ПК\n{LINE}")
        print("1. Текущий мониторинг\n2. Мониторинг за период\n3. Создать отчёт\n\n0. Выход\n")
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
            print("Неверный выбор. Введите 1, 2, 3 или 0.")
            time.sleep(1.2)
            continue
        try:
            action()
        except KeyboardInterrupt:
            continue
