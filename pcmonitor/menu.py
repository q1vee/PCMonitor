"""Интерактивное меню программы."""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

from . import display
from .collectors import collect_all, collect_smart
from .history import (PERIODS, HistoryStore, format_period_stats, parse_datetime, period_range,
                      period_stats, save_period_report)
from .thresholds import Thresholds, check

REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"
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


# --------------------------------------------------------------------------- #
# 1. Текущий мониторинг
# --------------------------------------------------------------------------- #
def live_monitoring(th: Thresholds, store: HistoryStore) -> None:
    raw = _ask("Интервал обновления, с (2–5, Enter — 3): ") or "3"
    try:
        interval = float(raw.replace(",", "."))
    except ValueError:
        interval = 3.0
    interval = min(5.0, max(2.0, interval))

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
            try:
                store.append(snap, alerts)
            except OSError as exc:
                alerts_note = f"Не удалось записать историю: {exc}"
            else:
                alerts_note = ""
            samples += 1

            screen = display.render(snap, alerts)
            footer = (f"\nОбновление каждые {interval:g} с · замеров: {samples} · история: {store.path.name}"
                      f"{' · ' + alerts_note if alerts_note else ''}\n"
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
    print(f"\n\nМониторинг остановлен. Записано замеров: {samples}.")
    time.sleep(1)


# --------------------------------------------------------------------------- #
# Выбор периода
# --------------------------------------------------------------------------- #
def choose_period(store: HistoryStore) -> tuple[datetime, datetime, str] | None:
    bounds = store.bounds()
    print(f"\n{LINE}\n  Выберите период\n{LINE}")
    if bounds:
        print(f"  История: {bounds[0]:%d.%m.%Y %H:%M} — {bounds[1]:%d.%m.%Y %H:%M}")
    else:
        print("  История пока пуста — сначала запустите «Текущий мониторинг».")
    keys = list(PERIODS)
    for i, k in enumerate(keys, 1):
        print(f"  {i}. {PERIODS[k][0]}")
    print(f"  {len(keys) + 1}. Произвольный период\n\n  0. Назад\n{LINE}")
    choice = _ask("Выберите период: ")
    if choice == "0":
        return None
    if choice in [str(i) for i in range(1, len(keys) + 1)]:
        return period_range(keys[int(choice) - 1])
    if choice == str(len(keys) + 1):
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
        return start, end, "Произвольный период"
    print("Неверный выбор.")
    return None


# --------------------------------------------------------------------------- #
# 2. Мониторинг за период
# --------------------------------------------------------------------------- #
def period_monitoring(store: HistoryStore) -> None:
    _clear()
    rng = choose_period(store)
    if rng:
        start, end, title = rng
        print()
        print(format_period_stats(period_stats(store.load(start, end), start, end, title)))
    _pause()


# --------------------------------------------------------------------------- #
# 3. Создать отчёт
# --------------------------------------------------------------------------- #
def create_report(store: HistoryStore) -> None:
    _clear()
    rng = choose_period(store)
    if not rng:
        _pause()
        return
    start, end, title = rng
    stats = period_stats(store.load(start, end), start, end, title)
    print(f"\n  Формат отчёта:\n  1. TXT (текст)\n  2. JSON\n  3. CSV (для Excel)")
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
