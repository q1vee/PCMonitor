"""Модульные тесты: python -m unittest discover -s tests -v"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime  # noqa: E402

from pcmonitor.history import (HistoryStore, format_period_stats, last_range, parse_datetime, period_range,  # noqa: E402
                               period_stats, save_period_report)
from pcmonitor.collectors import collect_cpu, collect_disks, collect_memory, collect_uptime  # noqa: E402
from pcmonitor.report import build_report, format_report  # noqa: E402
from pcmonitor.storage import MetricsLogger, read_log  # noqa: E402
from pcmonitor.thresholds import CRITICAL, WARNING, Thresholds, check  # noqa: E402


def make_snapshot(ts="2026-11-09T10:00:00", cpu=20.0, ram=40.0, temp=60.0, free_pct=50.0, health="OK"):
    return {
        "timestamp": ts, "host": "test", "os": "Test",
        "cpu": {"percent": cpu, "freq_mhz": 3000},
        "memory": {"percent": ram, "used_gb": 4.0, "swap_percent": 1.0},
        "temperature": {"available": True, "sensors": [{"name": "CPU", "celsius": temp}], "max": temp},
        "disks": [{"mount": "C:\\", "percent": 100 - free_pct, "free_gb": 100, "total_gb": 200, "fstype": "NTFS"}],
        "smart": {"available": True, "drives": [{"model": "SSD", "health": health}]},
        "uptime": {"seconds": 3600},
    }


class ThresholdTests(unittest.TestCase):
    def test_normal_values_no_alerts(self):
        self.assertEqual(check(make_snapshot(), Thresholds()), [])

    def test_temperature_warning_and_critical(self):
        a = check(make_snapshot(temp=88), Thresholds())
        self.assertEqual([x.level for x in a], [WARNING])
        a = check(make_snapshot(temp=97), Thresholds())
        self.assertEqual([x.level for x in a], [CRITICAL])

    def test_low_disk_cpu_ram_smart(self):
        a = check(make_snapshot(cpu=99, ram=95, free_pct=5, health="FAIL"), Thresholds())
        self.assertEqual({x.metric for x in a}, {"cpu", "ram", "disk", "smart"})

    def test_load_thresholds_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.json"
            p.write_text(json.dumps({"temp_warning": 70, "unknown": 1}), encoding="utf-8")
            t = Thresholds.load(p)
            self.assertEqual(t.temp_warning, 70)
            self.assertEqual(len(check(make_snapshot(temp=75), t)), 1)


class LogAndReportTests(unittest.TestCase):
    def _roundtrip(self, suffix):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / f"log{suffix}"
            lg = MetricsLogger(path)
            th = Thresholds()
            for i, (cpu, temp) in enumerate([(10, 50), (30, 90), (50, 70)]):
                s = make_snapshot(ts=f"2026-11-09T10:0{i}:00", cpu=cpu, temp=temp)
                lg.write(s, check(s, th))
            rows = read_log(path)
            self.assertEqual(len(rows), 3)
            rep = build_report(rows)
            self.assertEqual(rep["cpu_percent"], {"min": 10.0, "avg": 30.0, "max": 50.0})
            self.assertEqual(rep["samples_with_alerts"], 1)
            self.assertEqual(rep["duration"], "0:02:00")
            self.assertIn("ИТОГОВЫЙ ОТЧЁТ", format_report(rep))

    def test_csv(self):
        self._roundtrip(".csv")

    def test_jsonl(self):
        self._roundtrip(".jsonl")

    def test_broken_json_line_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "log.jsonl"
            p.write_text(json.dumps(make_snapshot()) + "\n{broken\n", encoding="utf-8")
            self.assertEqual(len(read_log(p)), 1)

    def test_empty_log_raises(self):
        with self.assertRaises(ValueError):
            build_report([])

    def test_missing_log_raises(self):
        with self.assertRaises(FileNotFoundError):
            read_log("нет_такого_файла.csv")


class HistoryTests(unittest.TestCase):
    def _store(self, d):
        store = HistoryStore(Path(d) / "h.jsonl")
        data = [  # время, cpu, ram, t_cpu, свободно на C:, предупреждение?
            ("2026-11-01T10:00:00", 10, 40, 50, 100.0, False),
            ("2026-11-09T10:00:00", 20, 50, 60, 90.0, False),
            ("2026-11-09T10:30:00", 80, 70, 90, 85.5, True),
            ("2026-11-09T11:00:00", 30, 60, 55, 80.0, False),
        ]
        th = Thresholds()
        for ts, cpu, ram, t, free, _ in data:
            s = make_snapshot(ts=ts, cpu=cpu, ram=ram, temp=t)
            s["temperature"].update(cpu=t, gpu=t - 10)
            s["disks"][0]["free_gb"] = free
            store.append(s, check(s, th))
        return store

    def test_period_filter_and_stats(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            start, end = datetime(2026, 11, 9, 9), datetime(2026, 11, 9, 12)
            recs = store.load(start, end)
            self.assertEqual(len(recs), 3)
            s = period_stats(recs, start, end, "тест")
            self.assertEqual(s["measurements"], 3)
            self.assertEqual(s["cpu"], {"avg": 43.3, "max": 80, "max_at": "2026-11-09T10:30:00"})
            self.assertEqual(s["ram"]["max"], 70)
            self.assertEqual(s["temp_cpu"], {"min": 55, "max": 90})
            self.assertEqual(s["temp_gpu"], {"min": 45, "max": 80})
            self.assertEqual(s["disks"]["C:\\"]["change_gb"], -10.0)
            self.assertEqual(s["warnings_total"], 1)
            self.assertIn("СТАТИСТИКА ЗА ПЕРИОД", format_period_stats(s))

    def test_last_n_and_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)  # 4 замера без сеанса
            for i in range(3):
                s = make_snapshot(ts=f"2026-11-09T12:00:0{i}")
                store.append(s, [], session="2026-11-09T12:00:00")
            self.assertEqual([r["ts"] for r in store.load_last(2)],
                             ["2026-11-09T12:00:01", "2026-11-09T12:00:02"])
            sessions = store.sessions()
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["count"], 3)
            self.assertEqual(len(store.load_session("2026-11-09T12:00:00")), 3)
            self.assertEqual(last_range(hours=5, now=datetime(2026, 11, 9, 12))[0], datetime(2026, 11, 9, 7))
            self.assertEqual(last_range(days=2, now=datetime(2026, 11, 9, 12))[0], datetime(2026, 11, 7, 12))

    def test_empty_period(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            start, end = datetime(2025, 1, 1), datetime(2025, 1, 2)
            s = period_stats(store.load(start, end), start, end)
            self.assertEqual(s["measurements"], 0)
            self.assertIn("нет данных", format_period_stats(s))

    def test_period_range_and_parse(self):
        now = datetime(2026, 11, 9, 12)
        self.assertEqual(period_range("week", now)[0], datetime(2026, 11, 2, 12))
        self.assertEqual(parse_datetime("09.11.2026 14:30"), datetime(2026, 11, 9, 14, 30))
        with self.assertRaises(ValueError):
            parse_datetime("вчера")

    def test_save_reports_all_formats(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            start, end = datetime(2026, 11, 1), datetime(2026, 11, 10)
            s = period_stats(store.load(start, end), start, end)
            for fmt in ("txt", "json", "csv"):
                p = save_period_report(s, Path(d) / fmt, fmt)
                self.assertTrue(p.exists() and p.stat().st_size > 0)


class CollectorTests(unittest.TestCase):
    """Проверка на реальной машине: сборщики возвращают осмысленные значения."""

    def test_cpu(self):
        c = collect_cpu(0.1)
        self.assertTrue(0 <= c["percent"] <= 100)

    def test_memory(self):
        m = collect_memory()
        self.assertGreater(m["total_gb"], 0)

    def test_disks(self):
        self.assertTrue(any("free_gb" in d for d in collect_disks()))

    def test_uptime(self):
        self.assertGreater(collect_uptime()["seconds"], 0)


if __name__ == "__main__":
    unittest.main()
